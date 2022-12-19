from time import sleep
from json import load, dumps

from ogr import GithubService, PagureService
from ogr.abstract import IssueStatus, PRStatus
from ogr.services.github import GithubIssue
from ogr.services.pagure import PagureIssue, PagurePullRequest


PAGURE_TOKEN = "pagure_token"
GITHUB_TOKEN = "gh_token"

BODY_TEMPLATE = "Original {what}: {link}\n" "Opened: {date}\n" "Opened by: {user}"
PAGURE_USERNAME = "INSERT_USERNAME"
DESCRIPTION_TEMPLATE = "\n\n{description}"
COMMENT_TEMPLATE = (
    "\n\n---\n\n#### [{user}](https://accounts.fedoraproject.org/user/{user})"
    " commented at {date}:\n{comment}"
)


SLEEP_SECONDS = 120
LAST_KNOWN_ID_ON_GH = 30
FIRST_MIGRATED_ISSUE = 31


JsonType = dict[str, "JsonType"] | dict[str, str]


class Transferator3000:
    def __init__(
        self,
        username: str,
        pagure_issues_json: list[JsonType],
        pagure_prs_json: list[JsonType],
    ) -> None:
        self.gh_service = GithubService(token=GITHUB_TOKEN)
        self.pagure_service = PagureService(
            token=PAGURE_TOKEN, instance_url="https://pagure.io"
        )

        self.gh_project = self.gh_service.get_project(
            namespace="fedora-copr", repo="copr"
        )
        self.pagure_project = self.pagure_service.get_project(
            namespace="copr", repo="copr", username=username
        )

        self.pagure_prs = {}
        self.pagure_issues = {
            issue.id: issue
            for issue in [
                PagureIssue(issue_dict, self.pagure_project)
                for issue_dict in pagure_issues_json
            ]
        }

    @staticmethod
    def _post_creation_of_issue(
        source_data: PagureIssue | PagurePullRequest, issue: GithubIssue
    ) -> None:
        if (
            isinstance(source_data, PagurePullRequest)
            or source_data.status == IssueStatus.closed
        ):
            issue.close()

        with open("./skript_log.txt", "a") as out:
            out.write(f"created issue id {issue.id}\n")

        sleep(SLEEP_SECONDS)

    def _create_issue(self, source_data: PagureIssue | PagurePullRequest) -> None:
        what = "PR" if isinstance(source_data, PagurePullRequest) else "issue"
        title = (
            "[PR] " + source_data.title
            if isinstance(source_data, PagurePullRequest)
            else source_data.title
        )

        issue = self.gh_project.create_issue(
            title=title,
            body=BODY_TEMPLATE.format(
                what=what,
                link=source_data.url,
                date=source_data.created,
                user=source_data.author,
            ),
        )
        assert issue.id == source_data.id

        self._post_creation_of_issue(source_data, issue)

    def transfer(self, id_matcher: int = 0) -> None:
        last_id = max(max(self.pagure_issues.keys()), max(self.pagure_prs.keys()))
        while id_matcher < last_id:
            id_matcher += 1
            source_data = self.pagure_issues.get(id_matcher)
            if source_data is None:
                source_data = self.pagure_prs.get(id_matcher)

            if source_data is None:
                self.gh_project.create_issue(
                    title="Dummy issue to fill space between IDs",
                    body="Dummy issue to fill space between IDs.",
                )
                sleep(SLEEP_SECONDS)
                continue

            self._create_issue(source_data)

    def _migrate_labels(self, pg_issue: PagureIssue) -> None:
        labels = pg_issue.labels
        if not labels:
            return

        gh_issue = self.gh_project.get_issue(pg_issue.id)
        if gh_issue.labels:
            return

        gh_issue.add_label(*labels)
        with open("./skript_log.txt", "a") as out:
            out.write(f"migrated labels and assignees; issue id {gh_issue.id}\n")

        sleep(SLEEP_SECONDS)

    @staticmethod
    def _is_migrated(issue: PagureIssue) -> bool:
        return issue._raw_issue["close_status"].upper() == "MIGRATED"

    def transfer_labels(self) -> None:
        for pg_issue in self.pagure_issues.values():
            if self._is_migrated(pg_issue):
                self._migrate_labels(pg_issue)

        for pg_issue in list(self.pagure_issues.values()):
            if not self._is_migrated(pg_issue):
                self._migrate_labels(pg_issue)

    @staticmethod
    def _get_pg_issue_content(issue: PagureIssue) -> str:
        result = DESCRIPTION_TEMPLATE.format(description=issue.description)
        comments = issue.get_comments()
        for comment in comments:
            if "This issue has been migrated to" in comment.body or "**Metadata Update from" in comment.body:
                continue

            result += COMMENT_TEMPLATE.format(
                user=comment.author,
                date=comment.created,
                comment=comment.body,
            )

        return result

    @staticmethod
    def _already_migrated(gh_issue: GithubIssue, pg_issue_content: str) -> bool:
        return pg_issue_content in gh_issue.description

    @staticmethod
    def _update_opened_by(original: str) -> str:
        for line in original.splitlines():
            if not "Opened by: " in line:
                continue

            user = line.split(":")[1].strip()
            text_to_replace = f"Opened by: [{user}](https://accounts.fedoraproject.org/user/{user})"
            return original.replace(line.strip(), text_to_replace)


    def update_issues_content(self) -> None:
        for pg_issue_id, pg_issue in self.pagure_issues.items():
            if pg_issue_id < FIRST_MIGRATED_ISSUE:
                continue

            gh_issue = self.gh_project.get_issue(pg_issue_id)
            assert gh_issue.id == pg_issue_id
            pg_issue_content = self._get_pg_issue_content(pg_issue)
            if self._already_migrated(gh_issue, pg_issue_content):
                continue

            gh_issue.description = self._update_opened_by(gh_issue.description) + pg_issue_content
            with open("./skript_log.txt", "a") as out:
                out.write(f"migrated issue content; issue id {gh_issue.id}\n")

            sleep(SLEEP_SECONDS)


class TransferComments:
    def __init__(self, username: str) -> None:
        self.pagure_service = PagureService(
            token=PAGURE_TOKEN, instance_url="https://pagure.io"
        )

        self.pagure_project = self.pagure_service.get_project(
            namespace="copr", repo="copr", username=username
        )

    def comment_and_close_on_pagure(self) -> None:
        opened_pagure_issues: list[PagureIssue] = self.pagure_project.get_issue_list(status=IssueStatus.open)
        for pg_issue in opened_pagure_issues:
            pg_issue.comment(
                f"This issue has been migrated to GitHub: https://github.com/fedora-copr/copr/issues/{pg_issue.id}"
            )

            # ogr can't close with custom status so this is updated version of
            # https://github.com/packit/ogr/blob/main/ogr/services/pagure/issue.py#L199
            payload = {"status": "Closed", "close_status": "MIGRATED"}
            pg_issue.project._call_project_api(
                "issue", str(pg_issue.id), "status", data=payload, method="POST"
            )
            pg_issue.__dirty = True

            with open("./skript_log.txt", "a") as out:
                out.write(f"closed issue on pagure: {pg_issue.id}\n")


def get_prs_json(issues: bool) -> JsonType:
    pagure_service = PagureService(token=PAGURE_TOKEN, instance_url="https://pagure.io")
    pagure_project = pagure_service.get_project(
        namespace="copr", repo="copr", username="test-acc"
    )

    if issues:
        return {
            "issues": [
                issue._raw_issue
                for issue in pagure_project.get_issue_list(IssueStatus.all)
            ]
        }

    return {"requests": [pr._raw_pr for pr in pagure_project.get_pr_list(PRStatus.all)]}


if __name__ == "__main__":
    with open("./pagure_issues.json") as pg_issues_file:
        pg_issues_data = load(pg_issues_file)

    # pass pagure username here
    transferator = Transferator3000(
        PAGURE_USERNAME, pg_issues_data["issues"], [{}]
    )
    transferator.update_issues_content()
