# News Summarizer Pipeline (English Business/World)

This project is an end-to-end news summarization pipeline. It fetches the latest English business/world news headlines from Google News via **SerpAPI**, selects the most interesting headlines using an LLM, scrapes supporting articles, generates bilingual summaries (Indonesian & English), and creates Instagram-ready posts from English summaries.

---

## Features

- Fetch top news headlines using SerpAPI.
- LLM selects the 5 most interesting headlines.
- Scrape supporting articles for each headline using Selenium + BeautifulSoup.
- Generate summaries in **Indonesian** and **English** using OpenAI.
- Generate Instagram post content from English summaries.
- Saves all outputs:
  - Headlines CSV
  - Supporting article text files
  - Summaries in `summary_<date>` folder
  - IG posts CSV with titles and post text

---

## Folder Structure

After running the pipeline, the folder structure looks like:
```
scraping_result/
├─ link_eng_business/
│ ├─ 2025-09-29/ <- news fetched today
│ │ ├─ Headline_1/
│ │ │ ├─ 1.txt
│ │ │ ├─ 2.txt
│ │ │ └─ ...
│ │ ├─ eng_business_headlines.csv
│ │ └─ eng_business_top5.csv
│ └─ summary_2025-09-29/ <- summaries and IG posts
│ ├─ id_sum_Headline_1.txt
│ ├─ en_sum_Headline_1.txt
│ └─ ig_posts_with_title.csv
```
