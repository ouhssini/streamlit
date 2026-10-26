import io
import re
import time
from urllib.parse import urlparse
import pandas as pd
from playwright.sync_api import sync_playwright
import requests
import streamlit as st
import os
import subprocess

# Auto-install Playwright Chromium binary if missing on Streamlit Cloud
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    pass

@st.cache_resource
def install_playwright():
    subprocess.run(["playwright", "install", "chromium"])

install_playwright()


st.set_page_config(
    page_title="Google Maps Lead & Email Scraper", page_icon="📍", layout="wide"
)

st.title("📍 Google Maps Lead & Email Scraper")

# Sidebar Controls
st.sidebar.header("Search Settings")
query = st.sidebar.text_input("Search Query", "Plumbers in Miami FL")
max_results = st.sidebar.slider("Number of Results", min_value=1, max_value=50, value=10)
scrape_emails_toggle = st.sidebar.checkbox(
    "Deep Scan: Extract Emails from Websites", value=True
)
headless = st.sidebar.checkbox("Headless Mode (Hide Browser)", value=True)

st.sidebar.header("Filter Results")
filter_option = st.sidebar.selectbox(
    "Website Filter",
    ["All Leads", "Only Without Website (No)", "Only With Website (Yes)"],
)

start_button = st.sidebar.button("🚀 Start Scraping", type="primary")

if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = None


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

        # 1. Check for mailto: links first
        mailto_matches = re.findall(
            r'mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', html
        )
        if mailto_matches:
            return mailto_matches[0]

        # 2. General Email Regex fallback
        email_pattern = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
        emails = re.findall(email_pattern, html)

        # Filter out junk (images, fonts, placeholders)
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
                ]
            )
        ]
        return filtered[0] if filtered else "N/A"

    except Exception:
        return "N/A"


def clean_review_count(review_raw_str: str) -> int:
    """Extracts only digits from review string (e.g. '(1,240)' -> 1240)."""
    digits = re.sub(r"[^\d]", "", review_raw_str)
    return int(digits) if digits else 0


def scrape_google_maps(
    search_query: str, count: int, is_headless: bool, scan_emails: bool, status_box
):
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=is_headless, slow_mo=50)
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
                'button:has-text("Accept all"), button:has-text("Reject all"), button:has-text("I agree")'
            ).first
            if consent_btn.is_visible(timeout=3000):
                consent_btn.click()
                time.sleep(1)
        except Exception:
            pass

        # Locate Feed
        feed_selector = 'div[role="feed"]'
        try:
            page.wait_for_selector(feed_selector, timeout=10000)
        except Exception:
            status_box.error(
                "Could not find search results feed. Try a broader search query."
            )
            browser.close()
            return []

        # Scroll to load listings
        status_box.info("Scrolling feed to load listings...")
        feed = page.locator(feed_selector)

        for _ in range(count // 3 + 2):
            listings = page.locator("a.hfpxzc").all()
            if len(listings) >= count:
                break
            feed.evaluate("(elem) => elem.scrollBy(0, 2000)")
            time.sleep(1.5)

        total_to_scrape = min(len(listings), count)
        status_box.info(f"Scraping details for {total_to_scrape} places...")
        progress_bar = st.progress(0)

        for idx in range(total_to_scrape):
            try:
                cards = page.locator("a.hfpxzc").all()
                if idx >= len(cards):
                    break

                cards[idx].click()
                time.sleep(1.8)

                # Name
                name_loc = page.locator("h1.DUwDvf")
                name = name_loc.inner_text() if name_loc.count() > 0 else "N/A"

                # Rating
                rating_loc = page.locator(
                    'div.F7nice span[aria-hidden="true"]'
                ).first
                rating = (
                    rating_loc.inner_text()
                    if rating_loc.count() > 0
                    else "N/A"
                )

                # Number of Reviews
                reviews_loc = page.locator(
                    "div.F7nice span span[aria-label]"
                ).first
                raw_reviews = (
                    reviews_loc.inner_text()
                    if reviews_loc.count() > 0
                    else "0"
                )
                number_of_reviews = clean_review_count(raw_reviews)

                # Category
                cat_loc = page.locator(
                    'button[jsaction="pane.rating.category"]'
                ).first
                category = (
                    cat_loc.inner_text() if cat_loc.count() > 0 else "N/A"
                )

                # Address
                addr_loc = page.locator(
                    'button[data-item-id="address"]'
                ).first
                address = (
                    addr_loc.inner_text() if addr_loc.count() > 0 else "N/A"
                )

                # Website
                web_loc = page.locator(
                    'a[data-item-id="authority"], a[aria-label*="website" i]'
                ).first
                website = "N/A"
                if web_loc.count() > 0:
                    website = web_loc.get_attribute("href") or "N/A"

                has_website = (
                    "No"
                    if (website == "N/A" or not website or website.strip() == "")
                    else "Yes"
                )

                # Phone
                phone_loc = page.locator(
                    'button[data-item-id^="phone:tel:"]'
                ).first
                phone = (
                    phone_loc.inner_text() if phone_loc.count() > 0 else "N/A"
                )

                # Email Scraping (via Website)
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
                        "Address": address.replace("\n", ", "),
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
                query, max_results, headless, scrape_emails_toggle, status_box
            )
            if data:
                st.session_state.scraped_data = pd.DataFrame(data)
                status_box.success(
                    f"Done! Scraped {len(data)} businesses successfully."
                )
            else:
                status_box.error(
                    "No data was extracted. Try checking your internet connection or search query."
                )

# Display Table & Excel Download
if st.session_state.scraped_data is not None:
    df = st.session_state.scraped_data.copy()

    # Apply Website Filter
    if filter_option == "Only Without Website (No)":
        df = df[df["Has Website"] == "No"]
    elif filter_option == "Only With Website (Yes)":
        df = df[df["Has Website"] == "Yes"]

    st.subheader(f"Results ({len(df)} records displayed)")
    st.dataframe(df, use_container_width=True)

    # Export to Excel in Memory
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
