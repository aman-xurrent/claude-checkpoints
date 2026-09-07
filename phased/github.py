"""One GraphQL call per repository per tick, for the registered pull requests only.
Authentication is gh's: nothing here reads a token."""
import json
import os
import subprocess

from decide import Comment, Facts, Review, Thread

PULL_REQUEST_FIELDS = """
  number url title isDraft closed merged headRefName
  commits(last:1){ nodes{ commit{ oid committedDate } } }
  latestReviews(first:20){ nodes{ id author{login} state submittedAt commit{ oid } } }
  reviewThreads(first:100){ nodes{ id isResolved isOutdated
    comments(last:20){ nodes{ author{login} body path line createdAt url } } } }
  comments(last:30){ nodes{ id author{login} body createdAt } }
"""


def gh(gh_host, *arguments, input_text=None):
    environment = {**os.environ, "GH_HOST": gh_host}
    completed = subprocess.run(["gh", *arguments], env=environment, capture_output=True, text=True,
                               input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL, timeout=120)
    if completed.returncode != 0:
        raise RuntimeError(f"gh {' '.join(arguments[:2])} failed: {completed.stderr.strip()[:400]}")
    return completed.stdout


def fetch(gh_host, repo, numbers):
    """Returns {number: (Facts, raw pull request dict)} for the numbers GitHub knows."""
    if not numbers:
        return {}
    owner, name = repo.split("/", 1)
    aliases = "\n".join(f"  pr{number}: pullRequest(number:{number}){{ {PULL_REQUEST_FIELDS} }}" for number in numbers)
    query = f"query($owner:String!,$name:String!){{ rateLimit{{ cost remaining }} repository(owner:$owner,name:$name){{\n{aliases}\n}} }}"
    output = gh(gh_host, "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}", "-f", f"query={query}")
    payload = json.loads(output)
    repository = (payload.get("data") or {}).get("repository") or {}
    results = {}
    for number in numbers:
        raw = repository.get(f"pr{number}")
        if raw:
            results[number] = (facts_from(raw), raw)
    return results, (payload.get("data") or {}).get("rateLimit") or {}


def facts_from(raw):
    commit = raw["commits"]["nodes"][0]["commit"] if raw["commits"]["nodes"] else {"oid": "", "committedDate": ""}
    reviews = tuple(Review(node["id"], (node.get("author") or {}).get("login", ""), node["state"], node["submittedAt"],
                           (node.get("commit") or {}).get("oid", "")) for node in raw["latestReviews"]["nodes"])
    threads = []
    for node in raw["reviewThreads"]["nodes"]:
        comments = node["comments"]["nodes"]
        last = comments[-1] if comments else {"createdAt": "1970-01-01T00:00:00Z", "author": {"login": ""}}
        threads.append(Thread(node["id"], node["isResolved"], node["isOutdated"], last["createdAt"],
                              (last.get("author") or {}).get("login", "")))
    comments = tuple(Comment(node["id"], (node.get("author") or {}).get("login", ""), node.get("body", ""), node["createdAt"])
                     for node in raw["comments"]["nodes"])
    return Facts(commit["oid"], commit["committedDate"], raw["isDraft"], raw["closed"], raw["merged"], reviews, tuple(threads), comments)


def set_label(gh_host, repo, number, label):
    """Best effort mirror for humans; never read back."""
    try:
        gh(gh_host, "label", "create", label, "--repo", repo, "--force", "--color", "5319e7", "--description", "phased development")
        gh(gh_host, "api", "-X", "POST", f"repos/{repo}/issues/{number}/labels", "-f", f"labels[]={label}")
        return True
    except RuntimeError:
        return False


def remove_label(gh_host, repo, number, label):
    try:
        gh(gh_host, "api", "-X", "DELETE", f"repos/{repo}/issues/{number}/labels/{label}")
    except RuntimeError:
        pass
