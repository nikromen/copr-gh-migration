from time import sleep
from json import load

from ogr import GithubService, PagureService
from ogr.abstract import IssueStatus, PRStatus
from ogr.services.github import GithubIssue
from ogr.services.pagure import PagureIssue, PagurePullRequest


PAGURE_TOKEN = "pagure_token"
GITHUB_TOKEN = "gh_token"

BODY_TEMPLATE = "Original {what}: {link}\n" "Opened: {date}\n" "Opened by: {user}"
PAGURE_USERNAME = "INSERT_USERNAME"

SLEEP_SECONDS = 120
LAST_KNOWN_ID_ON_GH = 30


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
            namespace="GH-test-bot", repo="copr"
        )
        self.pagure_project = self.pagure_service.get_project(
            namespace="copr", repo="copr", username=username
        )

        self.pagure_prs = {
            pr.id: pr
            for pr in [
                PagurePullRequest(pr_dict, self.pagure_project)
                for pr_dict in pagure_prs_json
            ]
        }
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

    with open("./pagure_prs.json") as pg_prs_file:
        pg_prs_data = load(pg_prs_file)

    # pass pagure username here
    transferator = Transferator3000(
        PAGURE_USERNAME, pg_issues_data["issues"], pg_prs_data["requests"]
    )
    transferator.transfer(LAST_KNOWN_ID_ON_GH)
