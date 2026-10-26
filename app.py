import io
import os
import re
import shutil
import subprocess
import time
from urllib.parse import urlparse
import pandas as pd
import requests
import streamlit as st


# --- STREAMLIT CLOUD CHROMIUM AUTO-INSTALL ---
@st.cache_resource
def setup_playwright():
    """Ensures Chromium binaries are installed on Streamlit Cloud"""
    subprocess.run(["playwright", "install", "chromium"])


setup_playwright()

from playwright.sync_api import sync_playwright

# --- UI SETUP ---
st.set_page_config(
    page_title="Google Maps Lead & Email Scraper", page_icon="📍", layout="wide"
)

st.title("📍 Google Maps Lead & Email Scraper")

# Sidebar
st.sidebar.header("Search Settings")
query = st.sidebar.text_input("Search Query", "Plumbers in Miami FL")
max_results = st.sidebar.slider(
    "Number of Results", min_value=1, max_value=50, value=10
)
scrape_emails_toggle = st.sidebar.checkbox(
    "Deep Scan: Extract Emails from Websites", value=True
)

st.sidebar.header("Filter Results")
filter_option = st.sidebar.selectbox(
    "Website Filter",
    ["All Leads", "Only Without Website (No)", "Only With Website (Yes)"],
)

start_button = st.sidebar.button("🚀 Start Scraping", type="primary")

if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = None


def clean_text_icons(text: str) -> str:
    """Removes Google Maps private-use font icons like  and  and cleans spacing."""
    if not text or text == "N/A":
        return "N/A"
    # Remove Unicode Private Use Area characters (\ue000 to \uf8ff)
    cleaned = re.sub(r"[\ue000-\uf8ff]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "N/A"


def extract_email_from_website(url: str) -> str:
    """Scans the business website HTML for email addresses."""
    if not url or url == "N/A":
        return "N/A"

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=5, verify=False)
        html = resp.text

        # 1. Look for mailto: links
        mailto_matches = re.findall(
            r"mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)", html
        )
        if mailto_matches:
            return mailto_matches[0]

        # 2. General Email Regex
        email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
        emails = re.findall(email_pattern, html)

        filtered = [
            e
            for e in emails
            if not any(
                ext in e.lower()
                for ext in [
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                    ".gif",
                    "sentry",
                    "wixpress",
                    "example.com",
                ]
            )
        ]
        return filtered[0] if filtered else "N/A"

    except Exception:
        return "N/A"


def scrape_google_maps(search_query: str, count: int, scan_emails: bool, status_box):
    results = []

    with sync_playwright() as p:
        system_chromium = shutil.which("chromium") or shutil.which("chromium-browser")
        launch_kwargs = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ],
        }
        if system_chromium:
            launch_kwargs["executable_path"] = system_chromium

        browser = p.chromium.launch(**launch_kwargs)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        status_box.info(f"Opening Google Maps for '{search_query}'...")
        page.goto(
            f"https://www.google.com/maps/search/{search_query.replace(' ', '+')}"
        )

        # Bypass Cookie/Consent Screen
        try:
            consent_btn = page.locator(
                'button:has-text("Accept all"), button:has-text("Reject all"), button:has-text("I agree"), button:has-text("Tout accepter")'
            ).first
            if consent_btn.is_visible(timeout=3000):
                consent_btn.click()
                time.sleep(1)
        except Exception:
            pass

        # Locate Feed
        feed_selector = 'div[role="feed"]'
        try:
            page.wait_for_selector(feed_selector, timeout=12000)
        except Exception:
            status_box.error(
                "Could not find search results. Try a broader search query."
            )
            browser.close()
            return []

        status_box.info("Scrolling feed to load listings...")
        feed = page.locator(feed_selector)

        for _ in range(count // 3 + 2):
            listings = page.locator("a.hfpxzc").all()
            if len(listings) >= count:
                break
            feed.evaluate("(elem) => elem.scrollBy(0, 2000)")
            time.sleep(1.5)

        total_to_scrape = min(len(listings), count)
        status_box.info(f"Scraping {total_to_scrape} locations...")
        progress_bar = st.progress(0)

        for idx in range(total_to_scrape):
            try:
                cards = page.locator("a.hfpxzc").all()
                if idx >= len(cards):
                    break

                cards[idx].click()
                time.sleep(2.0)  # Wait for panel to open and populate

                # CRITICAL: Scope strictly to the open Details Pane
                pane = page.locator('div[role="main"]')

                # 1. Business Name
                name_loc = pane.locator("h1.DUwDvf").first
                name = name_loc.inner_text().strip() if name_loc.count() > 0 else "N/A"

                # 2. Rating & Review Count
                rating = "N/A"
                number_of_reviews = 0

                rating_box = pane.locator("div.F7nice").first
                if rating_box.count() > 0:
                    box_text = rating_box.inner_text()

                    # Extract Rating (e.g., 4.9 or 4,9)
                    r_match = re.search(r"(\d+[.,]\d+)", box_text)
                    if r_match:
                        rating = r_match.group(1).replace(",", ".")

                    # Extract Number of Reviews (e.g. (14) or (1,240))
                    rev_match = re.search(r"\(([\d\s.,]+)\)", box_text)
                    if rev_match:
                        digits = re.sub(r"[^\d]", "", rev_match.group(1))
                        number_of_reviews = int(digits) if digits else 0

                # 3. Category
                cat_loc = pane.locator('button[jsaction*="category"], span.DkEaL').first
                category = (
                    cat_loc.inner_text().strip() if cat_loc.count() > 0 else "N/A"
                )

                # 4. Website (Strictly scoped inside pane to avoid inheriting other cards)
                web_loc = pane.locator('a[data-item-id="authority"]').first
                website = "N/A"
                if web_loc.count() > 0 and web_loc.is_visible():
                    href = web_loc.get_attribute("href")
                    if href and "google.com" not in href:
                        website = href

                has_website = "Yes" if website != "N/A" else "No"

                # 5. Phone
                phone_loc = pane.locator('button[data-item-id^="phone:tel:"]').first
                phone = "N/A"
                if phone_loc.count() > 0:
                    phone = clean_text_icons(phone_loc.inner_text())

                # 6. Address
                addr_loc = pane.locator('button[data-item-id="address"]').first
                address = "N/A"
                if addr_loc.count() > 0:
                    raw_addr = clean_text_icons(addr_loc.inner_text())
                    address = raw_addr.replace("\n", ", ").strip(", ")

                # 7. Email (ONLY searched if business actually has a website)
                email = "N/A"
                if scan_emails and has_website == "Yes":
                    email = extract_email_from_website(website)

                results.append(
                    {
                        "Name": name,
                        "Has Website": has_website,
                        "Email": email,
                        "Website": website,
                        "Phone": phone,
                        "Rating": rating,
                        "Number of Reviews": number_of_reviews,
                        "Category": category,
                        "Address": address,
                    }
                )

            except Exception:
                pass

            progress_bar.progress((idx + 1) / total_to_scrape)

        browser.close()

    return results


# UI Logic
status_box = st.empty()

if start_button:
    if not query.strip():
        st.warning("Please type a search query first.")
    else:
        with st.spinner("Extracting data and hunting emails..."):
            data = scrape_google_maps(
                query, max_results, scrape_emails_toggle, status_box
            )
            if data:
                st.session_state.scraped_data = pd.DataFrame(data)
                status_box.success(
                    f"Done! Scraped {len(data)} businesses successfully."
                )
            else:
                status_box.error("No data extracted. Verify your search term.")

# Display & Excel Download
if st.session_state.scraped_data is not None:
    df = st.session_state.scraped_data.copy()

    if filter_option == "Only Without Website (No)":
        df = df[df["Has Website"] == "No"]
    elif filter_option == "Only With Website (Yes)":
        df = df[df["Has Website"] == "Yes"]

    st.subheader(f"Results ({len(df)} records displayed)")
    st.dataframe(df, use_container_width=True)

    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Leads")
    excel_data = excel_buffer.getvalue()

    file_name = f"{query.replace(' ', '_').lower()}_leads.xlsx"

    st.download_button(
        label="📥 Download Data as Excel (.xlsx)",
        data=excel_data,
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
