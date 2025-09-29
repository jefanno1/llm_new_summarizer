# news_pipeline_all_in_one.py
import os
import csv
import json
import time
import re
from datetime import datetime
from typing import List, Optional
from dotenv import load_dotenv

from serpapi import GoogleSearch
from openai import OpenAI
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager

# ================
# CONFIG
# ================
# Prefer environment variables for keys

load_dotenv()  # load file .env

SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY") 

# topic token for eng business/world (you had this before)
TOPIC_TOKEN_BUSINESS = "CAAqJggKIiBDQkFTRWdvSUwyMHZNRGx6TVdZU0FtVnVHZ0pWVXlnQVAB"

# tuning
HEADLINE_LIMIT = 10       # ambil 10 headlines
LLM_SELECT_MODEL = "gpt-5-nano"   # model untuk memilih headlines (ubah sesuai kebutuhan)
LLM_SUMMARY_MODEL = "gpt-5-nano"   # model untuk ringkasan (ubah sesuai ketersediaan)
SUPPORTING_PER_HEADLINE = 20      # ambil sampai 20 supporting links per headline
SCRAPE_WAIT = 3        # seconds after driver.get()

BAD_LABELS = {"top news", "posts on x", "frequently asked questions"}

# output base dir
BASE_OUT = os.path.join("scraping_result", "link_eng_business")
os.makedirs(BASE_OUT, exist_ok=True)

# ================
# UTIL
# ================
def safe_filename(s: str, maxlen: int = 80) -> str:
    f = re.sub(r'[^A-Za-z0-9_\- ]+', '', s)
    f = f.strip().replace(" ", "_")
    return f[:maxlen] if len(f) > 0 else "untitled"

def unique_path_noext(base_noext: str, ext: str = "csv"):
    path = f"{base_noext}.{ext}"
    i = 1
    while os.path.exists(path):
        path = f"{base_noext}({i}).{ext}"
        i += 1
    return path

# ================
# SerpAPI headline fetcher
# ================
def fetch_headlines_serpapi(topic_token: str, limit: int = 10):
    params = {
        "engine": "google_news",
        "topic_token": topic_token,
        "hl": "en",
        "gl": "US",
        "api_key": SERPAPI_API_KEY
    }
    try:
        search = GoogleSearch(params)
        results = search.get_dict()
    except Exception as e:
        print("❌ Error fetching headlines:", e)
        return []

    news_results = results.get("news_results", [])[:limit]
    out = []
    for n in news_results:
        hl = n.get("highlight", {}) or {}
        title = (hl.get("title") or n.get("title") or "").strip()
        if not title or any(bad in title.lower() for bad in BAD_LABELS):
            continue
        link = hl.get("link") or n.get("link") or ""
        source = (hl.get("source") or {}).get("name") or (n.get("source") or {}).get("name") or ""
        date = hl.get("date") or n.get("date") or ""
        # find story_token (several places)
        story_token = None
        if hl.get("story_token"):
            story_token = hl.get("story_token")
        elif n.get("story_token"):
            story_token = n.get("story_token")
        else:
            for s in (n.get("stories") or []):
                st = s.get("story_token")
                title_s = (s.get("title") or "").strip().lower()
                if st and title_s and not any(bad in title_s for bad in BAD_LABELS):
                    story_token = st
                    break
            if not story_token:
                # fallback, take first story with token
                for s in (n.get("stories") or []):
                    if s.get("story_token"):
                        story_token = s.get("story_token")
                        break

        out.append({
            "Title": title,
            "Link": link,
            "Source": source,
            "Published": date,
            "StoryToken": story_token or ""
        })
    return out

# ================
# LLM helpers
# ================
def get_openai_client():
    return OpenAI(api_key=OPENAI_API_KEY)

def ask_llm_select_top5(headlines: List[dict]) -> List[int]:
    client = get_openai_client()
    prompt = "Here are 10 headlines. Choose 5 most interesting to a general reader. Answer ONLY a JSON object like {\"selected\": [1,2,3,4,5]} with indices (1-based).\n\n"
    for i, h in enumerate(headlines, start=1):
        prompt += f"{i}. {h['Title']}\n"
    # call
    resp = client.chat.completions.create(
        model=LLM_SELECT_MODEL,
        messages=[
            {"role": "system", "content": "You are an assistant that selects the most interesting news headlines."},
            {"role": "user", "content": prompt}
        ]    # do not pass unsupported temperature if model forbids; omit to use default
    )
    raw = resp.choices[0].message.content.strip()
    # clean code fences
    if raw.startswith("```"):
        # remove ``` and possible language label
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    # parse json robustly
    try:
        parsed = json.loads(raw)
        sel = parsed.get("selected", [])
        # ensure ints
        sel = [int(x) for x in sel][:5]
        return sel
    except Exception as e:
        print("❌ LLM selection parse error:", e)
        print("Raw from LLM:", raw)
        # fallback: choose first 5
        return list(range(1, min(6, len(headlines)+1)))

def ask_llm_summarize_two_langs(text: str) -> dict:
    """
    Returns dict with keys "id" and "en"
    """
    client = get_openai_client()
    prompt = (
        "Ringkas teks berikut dalam 2 bahasa (komprehensif, jelas, agak panjang).\n"
        "Output HARUS valid JSON exactly like:\n"
        '{ "id": "Ringkasan Bahasa Indonesia", "en": "English summary" }\n\n'
        "Teks:\n" + text
    )
    resp = client.chat.completions.create(
        model=LLM_SUMMARY_MODEL,
        messages=[
            {"role": "system", "content": "You are an assistant that summarizes news articles into Indonesian and English."},
            {"role": "user", "content": prompt}
        ]
    )
    raw = resp.choices[0].message.content.strip()
    # clean code fences
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        j = json.loads(raw)
        return {"id": j.get("id", "").strip(), "en": j.get("en", "").strip()}
    except Exception as e:
        print("❌ Error parsing summary JSON from LLM:", e)
        print("Raw:", raw[:400])
        # fallback: return raw as id and empty en
        return {"id": raw, "en": ""}

def ask_llm_igpost_from_text(summary_en: str) -> Optional[dict]:
    client = get_openai_client()
    prompt = (
        "Given the following English summary, produce JSON: {\"title\": \"short title (<=10 words)\", \"ig_post\": \"IG post text (one slide)\"}\n\n"
        f"Summary:\n{summary_en}"
    )
    resp = client.chat.completions.create(
        model="gpt-5-nano",
        messages=[
            {"role": "system", "content": "You are a social media copywriter."},
            {"role": "user", "content": prompt}
        ]
    )
    raw = resp.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        j = json.loads(raw)
        return {"title": j.get("title", "").strip(), "ig_post": j.get("ig_post", "").strip()}
    except Exception as e:
        print("❌ Error parsing IG JSON:", e)
        print("Raw:", raw[:400])
        return None

# ================
# Scraper (Selenium)
# ================
def make_selenium_driver(headless: bool = True):
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=chrome_options)
    return driver

def scrape_article_text(driver, url: str, wait_seconds: int = SCRAPE_WAIT) -> str:
    try:
        driver.get(url)
        time.sleep(wait_seconds)
        soup = BeautifulSoup(driver.page_source, "html.parser")
        # try common article selectors
        article = soup.find("article") or soup.find(role="main")
        if article:
            paras = [p.get_text().strip() for p in article.find_all("p") if p.get_text().strip()]
            return "\n".join(paras)
        # fallback: collect long <p> in body
        body = soup.body
        if body:
            paras = [p.get_text().strip() for p in body.find_all("p") if p.get_text().strip()]
            return "\n".join(paras)
        return ""
    except Exception as e:
        print("❌ Selenium scraping error:", e)
        return ""

# ================
# FULL PIPELINE
# ================
def run_full_pipeline():
    # 1) prepare date folder
    run_date = datetime.now().strftime("%Y-%m-%d")
    out_dir = os.path.join(BASE_OUT, run_date)
    os.makedirs(out_dir, exist_ok=True)

    # 2) fetch headlines
    print("🔎 Fetching headlines from SerpAPI...")
    headlines = fetch_headlines_serpapi(TOPIC_TOKEN_BUSINESS, limit=HEADLINE_LIMIT)
    if not headlines:
        print("No headlines -> exit")
        return

    # save headlines CSV
    base_noext = os.path.join(out_dir, "eng_business_headlines")
    csv_path = unique_path_noext(os.path.splitext(base_noext)[0], ext="csv")  # keep uniqueness
    # actually unique_path_noext expects base_noext without ext; adapt:
    csv_path = unique_path_noext(os.path.join(out_dir, "eng_business_headlines"), ext="csv")

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Title", "Link", "Source", "Published", "StoryToken"])
        writer.writeheader()
        for row in headlines:
            writer.writerow(row)
    print(f"✅ Saved headlines to {csv_path}")

    # 3) ask LLM to pick top5
    print("🤖 Asking LLM to pick top 5 from the headlines...")
    # ensure we pass exactly 10 (or available)
    top_selection_idx = ask_llm_select_top5(headlines[:HEADLINE_LIMIT])
    print("LLM selected indices:", top_selection_idx)

    selected = [headlines[i-1] for i in top_selection_idx if 1 <= i <= len(headlines)]
    # save top5 CSV
    top5_csv = os.path.join(out_dir, "eng_business_top5.csv")
    with open(top5_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["StoryToken", "Title", "Link"])
        writer.writeheader()
        for r in selected:
            writer.writerow({"StoryToken": r.get("StoryToken",""), "Title": r.get("Title",""), "Link": r.get("Link","")})
    print(f"✅ Saved top5 to {top5_csv}")

    # 4) for each selected headline: get supporting links from story_token (via SerpAPI) and scrape them
    driver = make_selenium_driver(headless=True)
    for idx, h in enumerate(selected, start=1):
        token = h.get("StoryToken")
        title = h.get("Title", "")[:60]
        safe = safe_filename(title, maxlen=60)
        headline_folder = os.path.join(out_dir, safe)
        os.makedirs(headline_folder, exist_ok=True)
        print(f"\n📂 Headline {idx}: {title}")
        if not token:
            print("⚠️ No story token for this headline, skipping fetch-of-supporting-links.")
            continue

        # get supporting links
        params = {
            "engine": "google_news",
            "story_token": token,
            "hl": "en",
            "gl": "US",
            "api_key": SERPAPI_API_KEY
        }
        try:
            search = GoogleSearch(params)
            results = search.get_dict()
            news_results = results.get("news_results", [])[:SUPPORTING_PER_HEADLINE]
            links = [nr.get("link") for nr in news_results if nr.get("link")]
        except Exception as e:
            print("❌ Error getting story links via SerpAPI:", e)
            links = []

        print(f"Found {len(links)} supporting links (limit requested: {SUPPORTING_PER_HEADLINE})")

        # scrape each link, save as 1.txt, 2.txt ...
        scraped_count = 0
        for i, link in enumerate(links, start=1):
            print(f" Scraping {i}/{len(links)}: {link}")
            text = scrape_article_text(driver, link)
            if text and len(text.strip()) > 50:
                fname = f"{i}.txt"
                with open(os.path.join(headline_folder, fname), "w", encoding="utf-8") as f:
                    f.write(text)
                scraped_count += 1
                print("  ✅ saved")
            else:
                print("  ⚠️ no article text found or too short -> skipped")
        print(f"Scraped {scraped_count}/{len(links)} supporting articles for headline '{title}'")

    driver.quit()

    # 5) Summarize per-headline (only folders that have txt files). Save results in summary_<date>
    summary_dir = os.path.join(BASE_OUT, f"summary_{run_date}")
    os.makedirs(summary_dir, exist_ok=True)
    print(f"\n📝 Summaries will be stored in: {summary_dir}")

    # iterate headline folders inside out_dir
    headline_folders = [os.path.join(out_dir, f) for f in os.listdir(out_dir)
                        if os.path.isdir(os.path.join(out_dir, f))]
    ig_rows = []
    for folder in headline_folders:
        txt_files = [os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".txt")]
        if not txt_files:
            print("⚠️ Folder empty, skip:", folder)
            continue
        combined = ""
        for t in txt_files:
            try:
                with open(t, "r", encoding="utf-8") as fi:
                    combined += fi.read() + "\n"
            except Exception:
                pass

        if not combined.strip():
            print("⚠️ Nothing to summarize in:", folder)
            continue

        summaries = ask_llm_summarize_two_langs(combined)
        # write to summary_dir with requested names
        folder_name = os.path.basename(folder)
        id_fname = os.path.join(summary_dir, f"id_sum_{folder_name}.txt")
        en_fname = os.path.join(summary_dir, f"en_sum_{folder_name}.txt")
        with open(id_fname, "w", encoding="utf-8") as f:
            f.write(summaries.get("id",""))
        with open(en_fname, "w", encoding="utf-8") as f:
            f.write(summaries.get("en",""))
        print("✅ Saved summaries:", id_fname, en_fname)

        # generate IG post from English summary (if exists)
        if summaries.get("en", "").strip():
            ig = ask_llm_igpost_from_text(summaries["en"])
            if ig:
                ig_rows.append({
                    "headline_folder": folder_name,
                    "title": ig.get("title",""),
                    "ig_post": ig.get("ig_post","")
                })

    # 6) Save IG posts CSV inside summary dir
    ig_csv = os.path.join(summary_dir, "ig_posts_with_title.csv")
    with open(ig_csv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["headline_folder","title","ig_post"])
        w.writeheader()
        w.writerows(ig_rows)
    print(f"\n✅ IG posts saved to: {ig_csv}")

    print("\n🏁 Pipeline finished. Outputs in:", out_dir, "and", summary_dir)

if __name__ == "__main__":
    run_full_pipeline()
