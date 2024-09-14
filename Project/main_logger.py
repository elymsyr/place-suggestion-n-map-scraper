from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import json, re, traceback
from selenium import webdriver
from contextlib import contextmanager
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types import content
# from mangum import Mangum

app = FastAPI()
# handler = Mangum(app)

def config_model():
    generation_config = {
        "temperature": 1,
        "top_p": 0.95,
        "top_k": 64,
        "max_output_tokens": 1000,
        "response_schema": content.Schema(
            type=content.Type.ARRAY,
            items=content.Schema(
                type=content.Type.OBJECT,
                required=[
                    "place_name",
                    "only_country_name",
                    "only_city_name",
                    "only_district_name",
                    "only_street_name"
                ],
                properties={
                    "place_name": content.Schema(
                        type=content.Type.STRING
                    ),
                    "only_country_name": content.Schema(
                        type=content.Type.STRING
                    ),
                    "only_city_name": content.Schema(
                        type=content.Type.STRING
                    ),
                    "only_district_name": content.Schema(
                        type=content.Type.STRING
                    ),                    
                    "only_street_name": content.Schema(
                        type=content.Type.STRING
                    )
                }
            )
        ),
        "response_mime_type": "application/json",
    }
    return genai.GenerativeModel(model_name="gemini-1.5-pro",generation_config=generation_config)

def extract_coordinates(text):
    # Regular expression patterns for both formats
    pattern_1 = r'!3d([-+]?\d*\.\d+)!4d([-+]?\d*\.\d+)!'  # Pattern for '!3d<lat>!4d<lon>!'
    pattern_2 = r'@([-+]?\d*\.\d+),([-+]?\d*\.\d+),'       # Pattern for '@<lat>,<lon>,'
    
    # Check the text against both patterns
    match_1 = re.search(pattern_1, text)
    match_2 = re.search(pattern_2, text)
    
    if match_1:
        # Extract coordinates from the first pattern
        latitude, longitude = match_1.groups()
    elif match_2:
        # Extract coordinates from the second pattern
        latitude, longitude = match_2.groups()
    else:
        # Return None if no coordinates found
        return None

    return float(latitude), float(longitude)

def fetch_place_data(query):
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}/?hl=en"
    json_result = search_google_maps(url=url, query=query)
    if not json_result or isinstance(json_result, str): 
        if not json_result: 
            json_result = {'status': 'error', 'place_name': query, 'url': url}
        else: json_result = {'status': json_result, 'place_name': query, 'url': url}
    return query, json_result

def stream_response(response, start):
    buffer: str = ""
    collecting = False
    for chunk in response:
        # Decode chunk and append it to the buffer
        buffer += chunk if isinstance(chunk, str) else chunk.text
        if not collecting:
            start_index = buffer.find('{')
            if start_index != -1:  # Changed condition to check for a valid start index
                collecting = True
        
        if collecting:
            end_index = buffer.find('}')
            if end_index != -1:  # Changed condition to check for a valid end index
                collecting = False
                start_index = buffer.find('{')
                if start_index != -1:
                    try:
                        response_data = json.loads(buffer[start_index:end_index + 1])
                        buffer = buffer[:start_index] + buffer[end_index + 1:]
                        response_data['timer'] = perf_counter() - start
                        yield response_data
                    except: continue

def scrap(query: str, gemini_api_key: str, max_worker: int, language: str):
    start = perf_counter()
    genai.configure(api_key=gemini_api_key)
    model = config_model()

    with ThreadPoolExecutor(max_workers=int(max_worker)) as executor:
        futures = []
        
        for chunk in stream_response(model.generate_content(f"{query} ({language=})", stream=True), start=start):
            try:
                places_query = f"{chunk['place_name']}, {chunk['only_street_name']}, {chunk['only_district_name']}, {chunk['only_city_name']}, {chunk['only_country_name']}"
                print(f"Chunk found in {chunk['timer']} s : ", places_query)            

                future = executor.submit(fetch_place_data, places_query)
                futures.append(future)

            except Exception as e:
                print(e, traceback.format_exc())

        for future in futures:
            query, json_result = future.result()
            if json_result:
                yield f"data: {json.dumps(json_result)}\n\n"
    print(f"End : {perf_counter()-start}")
    yield f"data: {{'status': 'complete','time': {perf_counter()-start}}}"
    return None

def search_google_maps(url, query, wait_time: int = 5, second_time = False):
    xpaths = {
        'place_name': "//div[contains(@class, 'tTVLSc')]//h1",
        'image_elements': "//div[contains(@class, 'tTVLSc')]//img",
        'place_type': "//div[contains(@class, 'skqShb')]//button[contains(@class, 'DkEaL')]",
        'place_type_back': "//div[contains(@class, 'skqShb')]",
        'price_elements': "//span[contains(@aria-label, 'Price')]",
        'if_result_url': "//div/a[contains(@class, 'hfpxzc')]",
        'patrial_matches': "//div[contains(@class, 'Bt0TOd')]"
    }    

    with get_driver() as (driver, creation_time):
        print("Started to : ", query)
        driver.get(url)
        data = {}
        print(url)
        start = perf_counter()
        timers = f"\n{query}\nDriver Created : {creation_time}"
        try:
            WebDriverWait(driver, wait_time).until(EC.presence_of_element_located((By.XPATH, xpaths['place_name'])))
            place_name_elements = driver.find_elements(By.XPATH, xpaths['place_name'])
            place_name = place_name_elements[0].text if place_name_elements else None    
            data['place_name'] = place_name
        except:
            if second_time: return f"No place name found : {query}"
        timers += f"\nTimer place_name {second_time}) : {perf_counter() - start}" ; start = perf_counter()

        try:
            WebDriverWait(driver, wait_time).until(EC.presence_of_element_located((By.XPATH, xpaths['image_elements'])))
            prefixes = ("https://lh5.googleusercontent.com/p/", "https://streetviewpixels-pa.googleapis.com")
            image_elements = driver.find_elements(By.XPATH, xpaths['image_elements'])
            image = [image.get_attribute('src') for image in image_elements if image.get_attribute('src').startswith(prefixes)] if image_elements else None
            data['image'] = image
        except:
            if second_time: return f"No image found : {query}"
        timers += f"\nTimer image_elements ({second_time}) : {perf_counter() - start}" ; start = perf_counter()

        patrial_matches_elements = driver.find_elements(By.XPATH, xpaths['patrial_matches'])
        if patrial_matches_elements:
            patrial_matches = [element.text for element in patrial_matches_elements][0]
            if patrial_matches and 'partial match'.strip() in patrial_matches.lower().strip(): return f"Partial matches found : {query}"

        place_type_elements = driver.find_elements(By.XPATH, "//div[contains(@class, 'skqShb')]//button[contains(@class, 'DkEaL')]")
        place_type = place_type_elements[0].text if place_type_elements else None
        data['place_type'] = place_type

        if place_type is None or place_type == '':
            place_type_elements = driver.find_elements(By.XPATH, "//div[contains(@class, 'skqShb')]")
            place_type = place_type_elements[0].text if place_type_elements else None

        price_elements = driver.find_elements(By.XPATH, "//span[contains(@aria-label, 'Price')]")
        price = price_elements[0].text if price_elements else None
        data['price'] = price

        timers += f"\nTimer rest elements ({second_time}) : {perf_counter() - start}" ; start = perf_counter()

        try:
            WebDriverWait(driver, wait_time).until(lambda driver: driver.current_url.startswith('https://www.google.com/maps/place'))
            data['url'] = driver.current_url
            data['coordinate'] = extract_coordinates(data['url'])
            print("Page loaded successfully : ", query, url)
        except:
            try:
                a_element = WebDriverWait(driver, wait_time*2).until(
                    EC.presence_of_element_located((By.XPATH, "//div/a[contains(@class, 'hfpxzc')]"))
                )
                print("Retrying : ", query)
                if not second_time: return search_google_maps(url=a_element.get_attribute("href"), query=query, second_time=True)
            except:
                return f"Error found : {query}"
        # print('Scraped : ', query)
        timers += f"\nTimer Scraped ({second_time}) : {perf_counter() - start}\n"
        print(timers)
        return data

@contextmanager
def get_driver():
    start = perf_counter()
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--no-sandbox')
    options.add_argument("--disable-images")
    options.add_argument("--disk-cache-size=0")
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('–disable-background-networking')
    options.add_argument('–disable-checker-imaging')
    options.add_argument('–disable-component-extensions-with-background-pages')
    options.add_argument('–disable-datasaver-prompt')
    options.add_argument('–disable-dev-shm-usage')
    options.add_argument('–disable-domain-reliability')
    options.add_argument('–disable-logging')
    options.add_argument('–disable-notifications')
    options.add_argument('–disable-renderer-backgrounding')
    
    driver = webdriver.Chrome(options=options)
    try:
        yield driver, perf_counter() - start
    finally:
        driver.quit()

@app.get("/scrap/")
async def scrape_task(query: str, gemini_api_key: str, maps_api_key: str = None, language: str = 'en', max_worker: int = 1):
    return StreamingResponse(scrap(query=query, gemini_api_key=gemini_api_key, language=language, max_worker=max_worker), media_type="text/event-stream")

@app.get("/")
async def read_root():
    return {
        "message": "Welcome to the Place Suggestion API!",
        "description": "This API provides place suggestions based on user prompts using Gemini AI.",
        "endpoints": {
            "/api/scrape": {
                "method": "GET",
                "description": "Get place suggestions based on a query.",
                "parameters": {
                    "query": "string, required - The search query for place suggestions.",
                    "gemini_api_key": "string, required - Your Gemini API key.",
                    "maps_api_key": "string, optional - Google Maps API key if you want to use Google Maps.",
                    "language": "string, optional - Language code, default is 'en'."
                }
            }
        },
        "author": "@elymsyr",
        "see project": "https://github.com/elymsyr/place-suggestion-api",
        "license": "GNU GENERAL PUBLIC LICENSE"
    }