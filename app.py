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
    page_title="Google Maps Lead & WhatsApp Scraper", page_icon="📍", layout="wide"
)

st.title("📍 Google Maps Lead & WhatsApp Scraper")

# Sidebar
st.sidebar.header("Search Settings")
query = st.sidebar.text_input("Search Query", "Cabinet d'avocat Casablanca")
max_results = st.sidebar.slider(
    "Target Number of Results", min_value=1, max_value=100, value=30
)
scrape_emails_toggle = st.sidebar.checkbox(
    "Deep Scan: Extract Emails & WhatsApp from Websites", value=True
)

st.sidebar.header("Filter Results")
filter_option = st.sidebar.selectbox(
    "Website Filter",
    ["All Leads", "Only Without Website (No)", "Only With Website (Yes)"],
)
wa_filter = st.sidebar.selectbox(
    "WhatsApp Filter",
    ["All Leads", "Has WhatsApp (Yes / Likely)", "No WhatsApp (Landline / None)"],
)

start_button = st.sidebar.button("🚀 Start Scraping", type="primary")

if "scraped_data" not in st.session_state:
    st.session_state.scraped_data = None


def clean_text_icons(text: str) -> str:
    """Removes Google Maps private-use font icons like  and  and cleans spacing."""
    if not text or text == "N/A":
        return "N/A"
    cleaned = re.sub(r"[\ue000-\uf8ff]", "", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned if cleaned else "N/A"


def extract_email_and_whatsapp_from_website(url: str) -> tuple[str, bool]:
    """Scans the business website HTML for email addresses and WhatsApp widgets."""
    if not url or url == "N/A":
        return ("N/A", False)

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

        # 1. Check for WhatsApp indicators on the website
        has_wa = bool(
            re.search(
                r"(wa\.me/|api\.whatsapp\.com|whatsapp://|send\?phone=)",
                html,
                re.I,
            )
        )

        # 2. Extract email
        email = "N/A"
        mailto_matches = re.findall(
            r'mailto:([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)', html
        )
        if mailto_matches:
            email = mailto_matches[0]
        else:
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
            if filtered:
                email = filtered[0]

        return (email, has_wa)

    except Exception:
        return ("N/A", False)


def check_whatsapp(phone: str, website_has_wa: bool) -> tuple[str, str]:
    """
    Validates WhatsApp capability using telecom prefix standards (Mobile vs Landline)
    and generates a direct 1-click wa.me chat link.
    """
    if not phone or phone == "N/A":
        return ("No (No Phone)", "N/A")

    digits = re.sub(r"[^\d]", "", phone)
    if not digits or len(digits) < 7:
        return ("No (Invalid Phone)", "N/A")

    wa_link = f"https://wa.me/{digits}"

    # 1. Directly verified on website
    if website_has_wa:
        return ("Yes (On Website)", wa_link)

    # 2. Morocco (+212): 6/7 are Mobile (WhatsApp), 5 is Landline (No WhatsApp)
    if digits.startswith("212"):
        sub = digits[3:]
        if sub.startswith(("6", "7")):
            return ("Yes (Mobile)", wa_link)
        elif sub.startswith("5"):
            return ("No (Landline)", wa_link)

    # 3. France (+33): 6/7 are Mobile, 1-5 are Landlines
    elif digits.startswith("33"):
        sub = digits[2:]
        if sub.startswith(("6", "7")):
            return ("Yes (Mobile)", wa_link)
        elif sub.startswith(("1", "2", "3", "4", "5")):
            return ("No (Landline)", wa_link)

    # 4. Spain (+34): 6/7 are Mobile, 8/9 are Landlines
    elif digits.startswith("34"):
        sub = digits[2:]
        if sub.startswith(("6", "7")):
            return ("Yes (Mobile)", wa_link)
        elif sub.startswith(("8", "9")):
            return ("No (Landline)", wa_link)

    # 5. United Kingdom (+44): 7 is Mobile, 1/2 are Landlines
    elif digits.startswith("44"):
        sub = digits[2:]
        if sub.startswith("7"):
            return ("Yes (Mobile)", wa_link)
        elif sub.startswith(("1", "2")):
            return ("No (Landline)", wa_link)

    # 6. US / Canada (+1)
    elif digits.startswith("1") and len(digits) == 11:
        return ("Likely (US/CA)", wa_link)

    # Default fallback for other international mobile structures
    return ("Likely", wa_link)


def scrape_google_maps(
    search_query: str, count: int, scan_emails: bool, status_box
):
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

        feed_selector = 'div[role="feed"]'
        try:
            page.wait_for_selector(feed_selector, timeout=12000)
        except Exception:
            status_box.error("Could not find search results. Try a broader search query.")
            browser.close()
            return []

        # --- STEP 1: PRE-HARVEST THE EXACT NUMBER OF URLs ---
        status_box.info(f"Searching and loading up to {count} listings...")
        feed = page.locator(feed_selector)
        place_urls = []
        consecutive_stuck = 0
        last_found = 0

        while len(place_urls) < count:
            elements = page.locator('div[role="feed"] a[href*="/maps/place/"]').all()
            for el in elements:
                try:
                    href = el.get_attribute("href")
                    if href and href not in place_urls:
                        place_urls.append(href)
                        if len(place_urls) >= count:
                            break
                except Exception:
                    continue

            if len(place_urls) >= count:
                break

            # Scroll feed
            feed.evaluate("(elem) => elem.scrollBy(0, 3000)")
            time.sleep(1.8)

            # Check if feed reached the end
            if len(place_urls) == last_found:
                consecutive_stuck += 1
                # Wiggle scroll to trigger lazy loading
                feed.evaluate("(elem) => elem.scrollBy(0, -600)")
                time.sleep(0.5)
                feed.evaluate("(elem) => elem.scrollBy(0, 3000)")
                time.sleep(1.8)

                if (
                    consecutive_stuck >= 6
                    or page.locator(
                        "text=You've reached the end of the list"
                    ).is_visible()
                ):
                    break
            else:
                consecutive_stuck = 0
                last_found = len(place_urls)
                status_box.info(
                    f"Found {len(place_urls)} of {count} listings. Scrolling..."
                )

        total_to_scrape = len(place_urls)
        status_box.info(f"Scraping detailed data for all {total_to_scrape} locations...")
        progress_bar = st.progress(0)

        # --- STEP 2: SCRAPE EACH PLACE DIRECTLY (Guarantees no missing/virtualized data) ---
        for idx, place_url in enumerate(place_urls):
            try:
                page.goto(place_url, timeout=20000, wait_until="domcontentloaded")
                time.sleep(1.5)

                pane = page.locator('div[role="main"]')

                # 1. Business Name
                name_loc = pane.locator("h1.DUwDvf").first
                name = (
                    name_loc.inner_text().strip()
                    if name_loc.count() > 0
                    else "N/A"
                )

                # 2. Rating & Review Count
                rating = "N/A"
                number_of_reviews = 0

                rating_box = pane.locator("div.F7nice").first
                if rating_box.count() > 0:
                    box_text = rating_box.inner_text()
                    r_match = re.search(r"(\d+[.,]\d+)", box_text)
                    if r_match:
                        rating = r_match.group(1).replace(",", ".")

                    rev_match = re.search(r"\(([\d\s.,]+)\)", box_text)
                    if rev_match:
                        digits = re.sub(r"[^\d]", "", rev_match.group(1))
                        number_of_reviews = int(digits) if digits else 0

                # 3. Category
                cat_loc = pane.locator(
                    'button[jsaction*="category"], span.DkEaL'
                ).first
                category = (
                    cat_loc.inner_text().strip()
                    if cat_loc.count() > 0
                    else "N/A"
                )

                # 4. Website
                web_loc = pane.locator('a[data-item-id="authority"]').first
                website = "N/A"
                if web_loc.count() > 0 and web_loc.is_visible():
                    href = web_loc.get_attribute("href")
                    if href and "google.com" not in href:
                        website = href

                has_website = "Yes" if website != "N/A" else "No"

                # 5. Phone
                phone_loc = pane.locator(
                    'button[data-item-id^="phone:tel:"]'
                ).first
                phone = "N/A"
                if phone_loc.count() > 0:
                    phone = clean_text_icons(phone_loc.inner_text())

                # 6. Address
                addr_loc = pane.locator(
                    'button[data-item-id="address"]'
                ).first
                address = "N/A"
                if addr_loc.count() > 0:
                    raw_addr = clean_text_icons(addr_loc.inner_text())
                    address = raw_addr.replace("\n", ", ").strip(", ")

                # 7. Website Scan (Email + WhatsApp presence)
                email = "N/A"
                website_has_wa = False
                if scan_emails and has_website == "Yes":
                    email, website_has_wa = (
                        extract_email_and_whatsapp_from_website(website)
                    )

                # 8. WhatsApp Status & Direct Chat Link
                has_whatsapp, wa_link = check_whatsapp(phone, website_has_wa)

                results.append(
                    {
                        "Name": name,
                        "Has WhatsApp": has_whatsapp,
                        "WhatsApp Link": wa_link,
                        "Phone": phone,
                        "Has Website": has_website,
                        "Email": email,
                        "Website": website,
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
        with st.spinner("Extracting data, verifying WhatsApp, and hunting emails..."):
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

    # Apply Website Filter
    if filter_option == "Only Without Website (No)":
        df = df[df["Has Website"] == "No"]
    elif filter_option == "Only With Website (Yes)":
        df = df[df["Has Website"] == "Yes"]

    # Apply WhatsApp Filter
    if wa_filter == "Has WhatsApp (Yes / Likely)":
        df = df[df["Has WhatsApp"].str.startswith(("Yes", "Likely"))]
    elif wa_filter == "No WhatsApp (Landline / None)":
        df = df[df["Has WhatsApp"].str.startswith("No")]

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