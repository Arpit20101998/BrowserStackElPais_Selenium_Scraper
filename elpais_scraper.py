import os
import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from collections import Counter
import re
from concurrent.futures import ThreadPoolExecutor

# THIS IMPORT IS CRUCIAL FOR DIRECT CREDENTIALS LOADING
from google.oauth2 import service_account

# --- Configuration ---
EL_PAIS_URL = "https://elpais.com/"
OPINION_SECTION_URL = "https://elpais.com/opinion/" # Direct link to the opinion section

# BrowserStack configuration - These will now be populated from user input
BROWSERSTACK_USERNAME = ""
BROWSERSTACK_ACCESS_KEY = ""

# Create a directory to save images if it doesn't exist
IMAGES_DIR = "article_images"
os.makedirs(IMAGES_DIR, exist_ok=True)

# --- Translation API (Google Cloud Translation API) ---
# This will now be populated from user input
YOUR_JSON_KEY_FILE_PATH = ""

# Initialize translate_client at the module level.
# It's set to None initially. Its actual value will be set by initialize_translation_client function.
translate_client = None

# --- Function to initialize the global translate_client ---
def initialize_translation_client(json_key_path):
    """Initializes the global Google Cloud Translation API client."""
    global translate_client # Declare global here, inside the function where it's being assigned
    try:
        from google.cloud import translate_v2 as translate
        if os.path.exists(json_key_path):
            credentials = service_account.Credentials.from_service_account_file(json_key_path)
            translate_client = translate.Client(credentials=credentials)
            print("Google Cloud Translation API initialized successfully using provided file path.")
        else:
            print(f"ERROR: Google Cloud Translation API JSON key file not found at: {json_key_path}")
            print("Translation features will be skipped.")
            translate_client = None
    except ImportError:
        print("Google Cloud Translation API library not found. Please install it: pip install google-cloud-translate")
        translate_client = None
    except Exception as e:
        print(f"Error initializing Google Cloud Translation API using provided path.")
        print(f"Error details: {e}")
        print("Translation features will be skipped.")
        translate_client = None

# --- Helper Functions for WebDriver Initialization ---

def get_webdriver_local():
    """Initializes and returns a local Chrome WebDriver."""
    print("Initializing local Chrome WebDriver...")
    service = Service(ChromeDriverManager().install()) # Automatically downloads ChromeDriver
    options = webdriver.ChromeOptions()
    # Attempt to force Spanish language preference in the browser
    options.add_argument("--lang=es")
    options.add_experimental_option('prefs', {'intl.accept_languages': 'es,es-ES'})
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()
    print("Local Chrome WebDriver initialized.")
    return driver

def get_webdriver_browserstack(browser_config):
    """Initializes and returns a BrowserStack Remote WebDriver based on config."""
    print(f"Initializing BrowserStack WebDriver for: {browser_config['browserName']} on {browser_config.get('os', 'N/A')} {browser_config.get('os_version', 'N/A')}...")

    if not BROWSERSTACK_USERNAME or not BROWSERSTACK_ACCESS_KEY:
        print("ERROR: BrowserStack credentials are not set. Please restart the script and provide them.")
        raise ValueError("BrowserStack credentials missing.")

    # Create browser-specific options object based on browserName
    browser_name = browser_config.get("browserName", "chrome").lower()
    if browser_name == "chrome":
        options = webdriver.ChromeOptions()
    elif browser_name == "firefox":
        options = webdriver.FirefoxOptions()
    elif browser_name == "edge":
        options = webdriver.EdgeOptions()
    elif browser_name == "safari":
        options = webdriver.SafariOptions()
    elif browser_name == "ie":
        options = webdriver.IeOptions()
    else:
        options = webdriver.ChromeOptions() # Default to Chrome options

    # Set standard W3C capabilities directly on the options object
    options.browser_name = browser_name # Ensures browserName is set correctly
    if "browserVersion" in browser_config:
        options.browser_version = browser_config["browserVersion"]

    # Define the BrowserStack specific options in a dictionary
    # ALL BrowserStack specific options go INSIDE this 'bstack:options' dictionary.
    bstack_options = {
        "userName": BROWSERSTACK_USERNAME,
        "accessKey": BROWSERSTACK_ACCESS_KEY,
        "projectName": "El Pais Scraper",
        "buildName": "Selenium Python Demo2",
        "sessionName": f"El Pais Opinion - {browser_config['browserName']} ({browser_config.get('device', browser_config.get('os_version', 'Desktop'))})",
        "seleniumVersion": "4.0.0",
        # These properties were causing the error. They should be handled either by
        # setting browser-specific preferences (if the browser supports it)
        # or by ensuring they are valid keys within bstack:options.
        # BrowserStack's current documentation suggests these might not be directly
        # settable as capabilities for all browsers, or they need to be passed
        # differently, e.g., as part of browser's own capabilities.
        # However, for simplicity and to remove the error, let's remove them
        # if they are not explicitly supported within bstack:options anymore.
        # For timezone/language, sometimes you can set browser preferences directly
        # if the browser supports it (e.g., Chrome options.add_argument("--lang=es")).
        # For BrowserStack, the 'browserstack.language' and 'browserstack.timezone'
        # are usually part of the legacy JSON Wire Protocol, not W3C.
        # The error says "additional properties [...] outside of the schema",
        # implying they are not valid keys directly within bstack:options for W3C.
    }

    # Add OS/device details from browser_config directly to bstack_options.
    if "os" in browser_config:
        bstack_options["os"] = browser_config["os"]
    if "os_version" in browser_config:
        bstack_options["osVersion"] = browser_config["os_version"] # BrowserStack uses "osVersion"
    if "device" in browser_config:
        bstack_options["deviceName"] = browser_config["device"] # BrowserStack uses "deviceName"
    if "realMobile" in browser_config:
        bstack_options["realMobile"] = browser_config["realMobile"]

    # Set the 'bstack:options' as a capability on the main options object
    options.set_capability("bstack:options", bstack_options)

    command_executor = f"https://{BROWSERSTACK_USERNAME}:{BROWSERSTACK_ACCESS_KEY}@hub-cloud.browserstack.com/wd/hub"

    driver = webdriver.Remote(
        command_executor=command_executor,
        options=options
    )
    print(f"BrowserStack WebDriver initialized for: {browser_config['browserName']}.")
    return driver

# --- Main Scraping and Processing Logic ---

def scrape_and_process_articles(driver):
    """
    Navigates to the Opinion section, scrapes article details, translates headers,
    and analyzes repeated words.
    """
    driver.get(OPINION_SECTION_URL)
    print(f"Navigating to El País Opinion section: {driver.current_url}")

    # Wait for articles to load on the page
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.TAG_NAME, "article"))
        )
        print("Articles loaded on the page.")
    except Exception as e:
        print(f"Timeout waiting for articles to load: {e}")
        return

    articles_elements = driver.find_elements(By.TAG_NAME, "article")
    print(f"Found {len(articles_elements)} article elements.")

    scraped_articles_data = []
    all_translated_headers = []

    for i, article_element in enumerate(articles_elements[:5]): # Process the first five articles
        print(f"\n--- Processing Article {i+1} ---")
        article_title = ""
        article_content = ""
        article_image_url = None

        try:
            # 1. Scrape Title
            try:
                title_element = article_element.find_element(By.TAG_NAME, "h2")
                article_title = title_element.text.strip()
                print(f"Title (Spanish): {article_title}")
            except Exception as e:
                print(f"Could not find title for article {i+1}: {e}")
                article_title = "Title not found"

            # 2. Scrape Content (Abstract/Summary)
            try:
                # El País often uses <p class="c_d"> for abstracts or summaries
                content_element = article_element.find_element(By.CSS_SELECTOR, "p.c_d")
                article_content = content_element.text.strip()
            except:
                # Fallback to other common elements if 'p.c_d' is not found
                try:
                    content_element = article_element.find_element(By.CSS_SELECTOR, "div.c_d_p") # Another common container
                    article_content = content_element.text.strip()
                except:
                    article_content = "Content summary not easily scraped from this section."
            print(f"Content (Spanish): {article_content}")

            # 3. Download Cover Image - REFINED LOGIC
            article_image_url = None
            try:
                img_element = None
                # Try more specific and common selectors for El País article images

                # Attempt 1: Image directly within a specific class for article media or within a figure
                try:
                    img_element = article_element.find_element(By.CSS_SELECTOR, "div.c_m img, figure img, img.c_m_e, img.c_d_m")
                except:
                    pass # If not found, move to the next attempt

                # Attempt 2: Handle <picture> tags (common for responsive images)
                if not img_element:
                    try:
                        picture_element = article_element.find_element(By.TAG_NAME, "picture")
                        # Try to find a <source> tag with srcset first
                        source_elements = picture_element.find_elements(By.TAG_NAME, "source")
                        for source in source_elements:
                            srcset = source.get_attribute("srcset")
                            if srcset and "http" in srcset:
                                # Take the first URL from the srcset list
                                article_image_url = srcset.split(',')[0].strip().split(' ')[0]
                                break
                        if not article_image_url: # If no suitable source, try img tag inside picture
                            img_element = picture_element.find_element(By.TAG_NAME, "img")
                    except:
                        pass # Move to next if <picture> or its contents not found

                # Attempt 3: Fallback to any img tag within the article element
                if not img_element and not article_image_url:
                    try:
                        img_element = article_element.find_element(By.TAG_NAME, "img")
                    except:
                        pass # No img element found

                if img_element:
                    # Prioritize data-srcset or data-src for lazy-loaded images, then src
                    possible_src_attrs = ["data-srcset", "data-src", "src"]
                    for attr in possible_src_attrs:
                        temp_url = img_element.get_attribute(attr)
                        if temp_url and "http" in temp_url: # Ensure it's a valid looking URL
                            article_image_url = temp_url
                            break

                if article_image_url and article_image_url.startswith("http"):
                    # Clean up URL if it contains multiple srcset values (e.g., "url1 1x, url2 2x")
                    if ',' in article_image_url:
                        article_image_url = article_image_url.split(',')[0].strip().split(' ')[0]

                    img_name = f"article_{i+1}_cover.jpg"
                    img_path = os.path.join(IMAGES_DIR, img_name)
                    print(f"Attempting to download image from: {article_image_url}")

                    # Add headers to mimic a real browser request, sometimes sites block direct requests
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                    }
                    response = requests.get(article_image_url, stream=True, timeout=10, headers=headers)

                    if response.status_code == 200:
                        with open(img_path, 'wb') as out_file:
                            out_file.write(response.content)
                        print(f"Cover image downloaded and saved to: {img_path}")
                    else:
                        print(f"Failed to download image from {article_image_url}. Status code: {response.status_code}")
                else:
                    print("No valid cover image URL found for this article after all attempts.")
            except Exception as img_e:
                print(f"General error during image handling for article {i+1}: {img_e}")


            # 4. Translate Article Header
            translated_title = ""
            # Check if translate_client was successfully initialized
            if translate_client and article_title != "Title not found":
                try:
                    translation = translate_client.translate(article_title, target_language='en')
                    translated_title = translation['translatedText']
                    print(f"Translated Title (English): {translated_title}")
                    all_translated_headers.append(translated_title)
                except Exception as e:
                    print(f"Error translating title '{article_title}': {e}")
                    translated_title = "Translation failed"
            else:
                print("Translation API not available or title not found, skipping translation for this article.")

            scraped_articles_data.append({
                "title_spanish": article_title,
                "content_spanish": article_content,
                "image_url": article_image_url,
                "title_english": translated_title
            })

        except Exception as e:
            print(f"An unexpected error occurred while processing article {i+1}: {e}")
            continue

    # 5. Analyze Translated Headers for Repeated Words
    if all_translated_headers:
        print("\n--- Analyzing Translated Headers for Repeated Words ---")
        all_words = []
        for header in all_translated_headers:
            # Use regex to find words, convert to lowercase, and extend the list
            words = re.findall(r'\b[a-zA-Z]+\b', header.lower()) # Only alpha characters, exclude numbers/symbols
            all_words.extend(words)

        word_counts = Counter(all_words)
        # Filter for words repeated more than twice
        repeated_words = {word: count for word, count in word_counts.items() if count > 2}

        if repeated_words:
            print("Words repeated more than twice across all translated headers:")
            for word, count in repeated_words.items():
                print(f"  - '{word}': {count} occurrences")
        else:
            print("No words repeated more than twice across all translated headers.")
    else:
        print("\nNo translated headers available for analysis.")

# --- Function to run a single test on BrowserStack ---
def run_browserstack_test(config):
    driver = None
    try:
        driver = get_webdriver_browserstack(config)
        scrape_and_process_articles(driver)
        # Mark session as passed on BrowserStack
        driver.execute_script('browserstack_executor: {"action": "setSessionStatus", "arguments": {"status": "passed", "reason": "Test completed successfully"}}')
        print(f"BrowserStack test on {config['browserName']} ({config.get('device', config.get('os_version', 'Desktop'))}) PASSED.")
    except Exception as e:
        # Mark session as failed on BrowserStack
        error_message = str(e).replace('"', '\\"') # Escape quotes for JSON
        if driver: # Only try to execute script if driver was initialized
            driver.execute_script(f'browserstack_executor: {{"action": "setSessionStatus", "arguments": {{"status": "failed", "reason": "Test failed: {error_message}"}}}}')
        print(f"BrowserStack test on {config['browserName']} ({config.get('device', config.get('os_version', 'Desktop'))}) FAILED: {e}")
    finally:
        if driver:
            driver.quit()
            print(f"BrowserStack WebDriver for {config['browserName']} ({config.get('device', config.get('os_version', 'Desktop'))}) closed.")

# --- Main Execution Block ---

if __name__ == "__main__":
    # --- Take inputs from CMD ---
    print("Please provide the required credentials and paths:")
    BROWSERSTACK_USERNAME = input("Enter your BrowserStack Username: ")
    BROWSERSTACK_ACCESS_KEY = input("Enter your BrowserStack Access Key: ")
    YOUR_JSON_KEY_FILE_PATH = input("Enter the full path to your Google Cloud Translation API JSON key file: ")

    # Call the helper function to initialize the global translate_client
    initialize_translation_client(YOUR_JSON_KEY_FILE_PATH)

    # --- Local Testing ---
    print("\nStarting LOCAL TEST...")
    local_driver = None
    try:
        local_driver = get_webdriver_local()
        scrape_and_process_articles(local_driver)
    except Exception as e:
        print(f"Local test encountered an error: {e}")
    finally:
        if local_driver:
            local_driver.quit()
            print("Local Chrome WebDriver closed.")

    # --- Cross-Browser Testing on BrowserStack ---
    print("\nStarting BROWSERSTACK CROSS-BROWSER TESTS...")

    # Define browser combinations for parallel testing on BrowserStack
    browserstack_test_configs = [
        # Desktop Browsers
        {"browserName": "chrome", "browserVersion": "latest", "os": "Windows", "os_version": "10"},
        {"browserName": "firefox", "browserVersion": "latest", "os": "Windows", "os_version": "10"},
        {"browserName": "edge", "browserVersion": "latest", "os": "Windows", "os_version": "10"},
        {"browserName": "chrome", "device": "Samsung Galaxy S22", "os": "android", "realMobile": "true"},
        {"browserName": "safari", "device": "iPhone 14 Pro", "os": "ios", "realMobile": "true"},
        {"browserName": "ie", "browserVersion": "11.0", "os": "Windows", "os_version": "7"}
    ]

    # Run tests in parallel using ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=len(browserstack_test_configs)) as executor:
        executor.map(run_browserstack_test, browserstack_test_configs)

    print("\nAll Selenium tests (local and BrowserStack) have completed.")
    print(f"Check the '{IMAGES_DIR}' directory for downloaded article images.")
    print("Visit your BrowserStack Automate dashboard to see the test results: https://automate.browserstack.com/dashboard")