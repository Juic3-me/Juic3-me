import datetime
import hashlib
import os
from xml.etree import ElementTree as ET

import requests

API = "https://api.github.com/graphql"
PANELS = [
    ("dark_mode.svg", "en"),
    ("light_mode.svg", "en"),
    ("dark_mode_ru.svg", "ru"),
    ("light_mode_ru.svg", "ru"),
]
ET.register_namespace("", "http://www.w3.org/2000/svg")


def birthday():
    raw = os.environ.get("BIRTHDAY") or "2006-01-01"
    return datetime.date.fromisoformat(raw)


def gql(query, variables):
    r = requests.post(
        API,
        json={"query": query, "variables": variables},
        headers={"Authorization": "token " + os.environ["ACCESS_TOKEN"]},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GraphQL {r.status_code}: {r.text}")
    data = r.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def plural(n, forms):
    if 11 <= n % 100 <= 14:
        return forms[2]
    if n % 10 == 1:
        return forms[0]
    if 2 <= n % 10 <= 4:
        return forms[1]
    return forms[2]


def uptime(lang):
    b, t = birthday(), datetime.date.today()
    y, m, d = t.year - b.year, t.month - b.month, t.day - b.day
    if d < 0:
        m -= 1
        d += (t.replace(day=1) - datetime.timedelta(days=1)).day
    if m < 0:
        y -= 1
        m += 12
    if lang == "ru":
        return (f"{y} {plural(y, ('год', 'года', 'лет'))}, "
                f"{m} {plural(m, ('месяц', 'месяца', 'месяцев'))}, "
                f"{d} {plural(d, ('день', 'дня', 'дней'))}")
    s = lambda n: "" if n == 1 else "s"
    return f"{y} year{s(y)}, {m} month{s(m)}, {d} day{s(d)}"


def basic_stats(login):
    q = """query($login: String!) { user(login: $login) {
        id
        followers { totalCount }
        repositories(ownerAffiliations: [OWNER]) { totalCount }
        contrib: repositories(ownerAffiliations: [COLLABORATOR, ORGANIZATION_MEMBER]) { totalCount }
    } }"""
    u = gql(q, {"login": login})["user"]
    return u["id"], u["followers"]["totalCount"], u["repositories"]["totalCount"], u["contrib"]["totalCount"]


def stars(login):
    q = """query($login: String!, $cursor: String) { user(login: $login) {
        repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
            nodes { stargazers { totalCount } }
            pageInfo { endCursor hasNextPage }
        } } }"""
    total, cursor = 0, None
    while True:
        d = gql(q, {"login": login, "cursor": cursor})["user"]["repositories"]
        total += sum(n["stargazers"]["totalCount"] for n in d["nodes"])
        if not d["pageInfo"]["hasNextPage"]:
            return total
        cursor = d["pageInfo"]["endCursor"]


def list_repos(login):
    q = """query($login: String!, $cursor: String) { user(login: $login) {
        repositories(first: 60, after: $cursor,
                     ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER]) {
            nodes { nameWithOwner defaultBranchRef { target { ... on Commit { history { totalCount } } } } }
            pageInfo { endCursor hasNextPage }
        } } }"""
    repos, cursor = [], None
    while True:
        d = gql(q, {"login": login, "cursor": cursor})["user"]["repositories"]
        for n in d["nodes"]:
            br = n["defaultBranchRef"]
            repos.append((n["nameWithOwner"], br["target"]["history"]["totalCount"] if br else 0))
        if not d["pageInfo"]["hasNextPage"]:
            return repos
        cursor = d["pageInfo"]["endCursor"]


def count_repo(full_name, uid):
    owner, name = full_name.split("/")
    q = """query($owner: String!, $name: String!, $cursor: String) {
        repository(owner: $owner, name: $name) { defaultBranchRef { target { ... on Commit {
            history(first: 100, after: $cursor) {
                nodes { additions deletions author { user { id } } }
                pageInfo { endCursor hasNextPage }
            } } } } } }"""
    commits = add = dele = 0
    cursor = None
    while True:
        h = gql(q, {"owner": owner, "name": name, "cursor": cursor})["repository"]["defaultBranchRef"]["target"]["history"]
        for n in h["nodes"]:
            if n["author"]["user"] and n["author"]["user"]["id"] == uid:
                commits += 1
                add += n["additions"]
                dele += n["deletions"]
        if not h["pageInfo"]["hasNextPage"]:
            return commits, add, dele
        cursor = h["pageInfo"]["endCursor"]


def loc_stats(login, uid):
    os.makedirs("cache", exist_ok=True)
    path = "cache/" + hashlib.sha256(login.encode()).hexdigest() + ".txt"
    old = {}
    if os.path.exists(path):
        for line in open(path):
            k, total, c, a, d = line.split()
            old[k] = (int(total), int(c), int(a), int(d))
    new = {}
    for full, total in list_repos(login):
        k = hashlib.sha256(full.encode()).hexdigest()
        if k in old and old[k][0] == total:
            new[k] = old[k]
        elif total == 0:
            new[k] = (0, 0, 0, 0)
        else:
            c, a, d = count_repo(full, uid)
            new[k] = (total, c, a, d)
    with open(path, "w") as f:
        for k, v in new.items():
            f.write(f"{k} {v[0]} {v[1]} {v[2]} {v[3]}\n")
    return sum(v[1] for v in new.values()), sum(v[2] for v in new.values()), sum(v[3] for v in new.values())


def dots(value, w):
    n = w - len(value)
    if n <= 2:
        return ["", " ", ". "][max(n, 0)]
    return " " + "." * (n - 2) + " "


def put(root, el_id, value):
    value = str(value)
    for el in root.iter():
        if el.get("id") == el_id:
            el.text = value
        elif el.get("id") == el_id + "_dots":
            el.text = dots(value, int(el.get("w", "0")))


def update_svg(path, data):
    tree = ET.parse(path)
    for k, v in data.items():
        put(tree.getroot(), k, v)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def main():
    login = os.environ["USER_NAME"]
    uid, followers, repos, contrib = basic_stats(login)
    commits, add, dele = loc_stats(login, uid)
    data = {
        "repo_data": f"{repos:,}",
        "contrib_data": f"{contrib:,}",
        "star_data": f"{stars(login):,}",
        "commit_data": f"{commits:,}",
        "follower_data": f"{followers:,}",
        "loc_data": f"{add - dele:,}",
        "loc_add": f"{add:,}",
        "loc_del": f"{dele:,}",
    }
    for path, lang in PANELS:
        update_svg(path, {**data, "age_data": uptime(lang)})


if __name__ == "__main__":
    main()
