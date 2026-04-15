import asyncio
from playwright.async_api import async_playwright
import json
import os

# -------------------------------
# CONFIG
# -------------------------------
BASE_URL = "https://www.espncricinfo.com"

SERIES_ID = "1298134"
SERIES_NAME = "icc-men-s-t20-world-cup-2022-23"

MATCH_RESULTS_URL = f"{BASE_URL}/series/{SERIES_NAME}-{SERIES_ID}/match-results"

OUTPUT_PATH = r"C:\Users\HP\Documents\Dbaver"
os.makedirs(OUTPUT_PATH, exist_ok=True)

DEBUG = True

def log(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")

print("📁 Saving JSON to:", OUTPUT_PATH)


# -------------------------------
# STAGE 1 → MATCH LINKS
# -------------------------------
async def get_match_links(page):
    log("Opening match results page...")
    await page.goto(MATCH_RESULTS_URL)

    await page.wait_for_timeout(6000)

    anchors = await page.query_selector_all("a[href*='full-scorecard']")
    log(f"Raw links found: {len(anchors)}")

    match_links = set()

    for a in anchors:
        href = await a.get_attribute("href")
        if href:
            match_links.add(BASE_URL + href)

    match_links = list(match_links)

    log(f"✅ Unique matches: {len(match_links)}")
    return match_links


# -------------------------------
# TEAM EXTRACTION (HYBRID)
# -------------------------------
async def extract_teams(page, url):
    # 1️⃣ Try DOM (scorecard header)
    try:
        headers = await page.locator("text=ovs").all()

        teams = []
        for h in headers:
            text = await h.inner_text()
            team = text.split("(")[0].strip()

            if team and team not in teams:
                teams.append(team)

        if len(teams) >= 2:
            return teams[0], teams[1]

    except:
        pass

    # 2️⃣ Fallback → URL based
    try:
        slug = url.split("/")[-2]
        parts = slug.split("-vs-")

        team1 = parts[0].replace("-", " ").title()
        team2 = parts[1].split("-")[0].replace("-", " ").title()

        return team1, team2

    except:
        return None, None


# -------------------------------
# STAGE 2 → MATCH DETAILS
# -------------------------------
async def parse_match(page, url):
    log(f"Opening match: {url}")
    await page.goto(url)
    await page.wait_for_timeout(5000)

    team1, team2 = await extract_teams(page, url)

    if not team1 or not team2:
        log("❌ Team extraction failed")
        return None

    match = f"{team1} Vs {team2}"

    # ---------- Batting ----------
    batting = []
    bat_tables = await page.query_selector_all("table.ci-scorecard-table")

    for i in range(min(2, len(bat_tables))):
        team = team1 if i == 0 else team2
        rows = await bat_tables[i].query_selector_all("tbody tr")

        for idx, row in enumerate(rows):
            cols = await row.query_selector_all("td")

            if len(cols) < 8:
                continue

            batting.append({
                "match": match,
                "team": team,
                "position": idx + 1,
                "batsman": await cols[0].inner_text(),
                "dismissal": await cols[1].inner_text(),
                "runs": await cols[2].inner_text(),
                "balls": await cols[3].inner_text(),
                "4s": await cols[5].inner_text(),
                "6s": await cols[6].inner_text(),
                "sr": await cols[7].inner_text()
            })

    # ---------- Bowling ----------
    bowling = []
    bowl_tables = await page.query_selector_all("table.ds-table")

    innings_map = [(1, team2), (3, team1)]

    for idx, team in innings_map:
        if idx >= len(bowl_tables):
            continue

        rows = await bowl_tables[idx].query_selector_all("tbody tr")

        for row in rows:
            cols = await row.query_selector_all("td")

            if len(cols) < 11:
                continue

            bowling.append({
                "match": match,
                "team": team,
                "bowler": await cols[0].inner_text(),
                "overs": await cols[1].inner_text(),
                "runs": await cols[3].inner_text(),
                "wickets": await cols[4].inner_text(),
                "economy": await cols[5].inner_text()
            })

    # ---------- Player Links ----------
    player_links = set()
    players = await page.query_selector_all("a[href*='/cricketers/']")

    for p in players:
        href = await p.get_attribute("href")
        if href:
            player_links.add(BASE_URL + href)

    return {
        "match": match,
        "batting": batting,
        "bowling": bowling,
        "players": list(player_links)
    }


# -------------------------------
# STAGE 3 → PLAYER INFO
# -------------------------------
async def get_player(page, url):
    await page.goto(url)
    await page.wait_for_timeout(2000)

    async def get_field(label):
        try:
            locator = page.locator(f"text={label}").first
            value = await locator.locator("xpath=..").locator("span").inner_text()
            return value
        except:
            return None

    try:
        name = await page.locator("h1").first.inner_text()
    except:
        name = None

    return {
        "name": name,
        "battingStyle": await get_field("Batting Style"),
        "bowlingStyle": await get_field("Bowling Style"),
        "role": await get_field("Playing Role"),
        "url": url
    }


# -------------------------------
# MAIN PIPELINE
# -------------------------------
async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)

        context = await browser.new_context(
            user_agent="Mozilla/5.0",
            viewport={"width": 1280, "height": 800}
        )

        await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {get: () => undefined})
        """)

        page = await context.new_page()

        match_links = await get_match_links(page)

        all_matches = []
        player_data = {}

        for i, link in enumerate(match_links[:5]):  # increase later
            print(f"🔄 Processing Match {i+1}")

            match_data = await parse_match(page, link)

            if not match_data:
                continue

            all_matches.append(match_data)

            for p_link in match_data["players"]:
                if p_link not in player_data:
                    player_data[p_link] = await get_player(page, p_link)

        await browser.close()

        # -------------------------------
        # SAVE JSON
        # -------------------------------
        with open(os.path.join(OUTPUT_PATH, "matches.json"), "w", encoding="utf-8") as f:
            json.dump(all_matches, f, indent=4, ensure_ascii=False)

        with open(os.path.join(OUTPUT_PATH, "players.json"), "w", encoding="utf-8") as f:
            json.dump(list(player_data.values()), f, indent=4, ensure_ascii=False)

        print("\n✅ DONE — JSON files saved at:", OUTPUT_PATH)


# RUN
asyncio.run(main())