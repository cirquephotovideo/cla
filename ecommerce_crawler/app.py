from flask import Flask, render_template, request, jsonify, Response
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime
import json
from playwright.sync_api import sync_playwright
import re
import os
import queue
import threading
from time import sleep
import logging
from logging.handlers import RotatingFileHandler
import uuid
import redis
from json import dumps, loads

app = Flask(__name__)
scheduler = BackgroundScheduler()
scheduler.start()

# Redis connection
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)

# Configure logging
if not os.path.exists('logs'):
    os.makedirs('logs')

file_handler = RotatingFileHandler('logs/app.log', maxBytes=1024 * 1024, backupCount=10)
file_handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
))
file_handler.setLevel(logging.INFO)
app.logger.addHandler(file_handler)
app.logger.setLevel(logging.INFO)
app.logger.info('E-commerce Crawler startup')

# Store crawling jobs and their results
crawling_jobs = {}
crawling_results = {}
log_queue = queue.Queue()

def log_message(message, level="info"):
    """Send a log message both to the Flask logger and the real-time log queue"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = {
        "timestamp": timestamp,
        "message": message,
        "level": level
    }
    log_queue.put(log_entry)
    
    # Also log to Flask logger
    if level == "error":
        app.logger.error(message)
    elif level == "warning":
        app.logger.warning(message)
    else:
        app.logger.info(message)

def store_job_status(job_id, status, results=None, error=None):
    """Store job status and results in Redis"""
    job_data = {
        'status': status,
        'timestamp': datetime.now().isoformat()
    }
    
    if results is not None:
        job_data['results'] = results
    if error is not None:
        job_data['error'] = str(error)
    
    redis_client.set(f'job:{job_id}', dumps(job_data))
    redis_client.expire(f'job:{job_id}', 3600)  # Expire after 1 hour

def get_job_status(job_id):
    """Get job status from Redis"""
    job_data = redis_client.get(f'job:{job_id}')
    return loads(job_data) if job_data else None

class EcommerceCrawler:
    def __init__(self, custom_selectors=None):
        log_message("Initializing crawler...")
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=True)
        self.custom_selectors = custom_selectors or {}
        log_message("Browser launched successfully")

    def extract_product_data(self, page):
        """Extract product information from the page using custom or default selectors"""
        try:
            log_message(f"Extracting data from page: {page.url}")
            data = {
                'url': page.url,
                'product_name': '',
                'description': '',
                'images': [],
                'regular_price': None,
                'promotional_price': None,
                'stock_status': None,
                'specifications': {},
                'brand': None,
                'categories': [],
                'rating': None,
                'sku': None,
                'variants': [],
                'additional_data': {}
            }
            
            # Extract product name using custom or default selectors
            selectors = {
                'name': self.custom_selectors.get('name', [
                    'h1.product-name',
                    'h1.product-title',
                    'h1[itemprop="name"]',
                    '.product-title h1',
                    '[data-testid="product-title"]',
                    '.page-title'
                ]),
                'description': self.custom_selectors.get('description', [
                    '[itemprop="description"]',
                    '.product-description',
                    '#description',
                    '.description',
                    'meta[name="description"]'
                ]),
                'price': self.custom_selectors.get('price', [
                    '[itemprop="price"]',
                    '.product-price',
                    '.price',
                    '[data-testid="price"]',
                    '.current-price',
                    '.price-box .price'
                ]),
                'images': self.custom_selectors.get('images', [
                    '[itemprop="image"]',
                    '.product-image img',
                    '.gallery img',
                    '[data-testid="product-image"]',
                    '.product-photo img'
                ]),
                'sku': self.custom_selectors.get('sku', [
                    '[itemprop="sku"]',
                    '[data-testid="product-sku"]',
                    '.sku',
                    '#product_reference',
                    '[data-sku]'
                ]),
                'stock': self.custom_selectors.get('stock', [
                    '[itemprop="availability"]',
                    '.stock-info',
                    '[data-testid="stock-status"]',
                    '.availability'
                ]),
                'categories': self.custom_selectors.get('categories', [
                    '.breadcrumb',
                    '.breadcrumbs',
                    '[itemtype="http://schema.org/BreadcrumbList"]',
                    '.navigation-path'
                ]),
                'brand': self.custom_selectors.get('brand', [
                    '[itemprop="brand"]',
                    '.product-brand',
                    '.brand',
                    'meta[property="product:brand"]'
                ])
            }

            # Function to try multiple selectors
            def try_selectors(selector_list, extract_type='text'):
                for selector in selector_list:
                    try:
                        elements = page.query_selector_all(selector)
                        if elements:
                            if extract_type == 'text':
                                return [el.inner_text().strip() for el in elements if el.inner_text().strip()]
                            elif extract_type == 'attribute':
                                return [el.get_attribute('src') or el.get_attribute('content') or el.get_attribute('data-src') for el in elements]
                    except Exception as e:
                        log_message(f"Error with selector {selector}: {str(e)}", "error")
                return []

            # Check if this is a product page or category page
            is_product_page = False
            product_grid_selectors = [
                '.product-grid',
                '.products-grid',
                '.product-list',
                '.category-products'
            ]
            
            for selector in product_grid_selectors:
                if page.query_selector(selector):
                    log_message("Detected category page, looking for product links...")
                    product_links = page.query_selector_all('a[href*="product"], .product-item a, .item-product a')
                    if product_links:
                        data['additional_data']['product_links'] = [link.get_attribute('href') for link in product_links]
                        log_message(f"Found {len(data['additional_data']['product_links'])} product links")
                    return data

            is_product_page = True
            log_message("Detected product page, extracting details...")

            # Extract data using selectors
            try:
                names = try_selectors(selectors['name'])
                data['product_name'] = names[0] if names else page.title()
                log_message(f"Found product name: {data['product_name']}")
            except Exception as e:
                log_message(f"Failed to extract product name: {str(e)}", "error")

            try:
                descriptions = try_selectors(selectors['description'])
                data['description'] = descriptions[0] if descriptions else ""
                log_message("Description extracted successfully")
            except Exception as e:
                log_message(f"Failed to extract description: {str(e)}", "error")

            try:
                images = try_selectors(selectors['images'], 'attribute')
                data['images'] = [img for img in images if img]
                log_message(f"Found {len(data['images'])} product images")
            except Exception as e:
                log_message(f"Failed to extract images: {str(e)}", "error")

            try:
                prices = try_selectors(selectors['price'])
                if prices:
                    # Clean up price text
                    price_text = prices[0].replace('€', '').replace('$', '').strip()
                    data['regular_price'] = float(price_text)
                    if len(prices) > 1:
                        promo_text = prices[1].replace('€', '').replace('$', '').strip()
                        data['promotional_price'] = float(promo_text)
                    log_message(f"Found price: {data['regular_price']}")
            except Exception as e:
                log_message(f"Failed to extract price: {str(e)}", "error")

            try:
                skus = try_selectors(selectors['sku'])
                data['sku'] = skus[0] if skus else None
                if data['sku']:
                    log_message(f"Found SKU: {data['sku']}")
            except Exception as e:
                log_message(f"Failed to extract SKU: {str(e)}", "error")

            try:
                stock_info = try_selectors(selectors['stock'])
                if stock_info:
                    data['stock_status'] = stock_info[0]
                    log_message(f"Found stock status: {data['stock_status']}")
            except Exception as e:
                log_message(f"Failed to extract stock status: {str(e)}", "error")

            try:
                categories = try_selectors(selectors['categories'])
                if categories:
                    # Clean up category text and split into list
                    cat_text = categories[0].replace('>', '/').replace('»', '/').replace('›', '/')
                    data['categories'] = [cat.strip() for cat in cat_text.split('/') if cat.strip()]
                    log_message(f"Found categories: {', '.join(data['categories'])}")
            except Exception as e:
                log_message(f"Failed to extract categories: {str(e)}", "error")

            try:
                brands = try_selectors(selectors['brand'])
                if brands:
                    data['brand'] = brands[0]
                    log_message(f"Found brand: {data['brand']}")
            except Exception as e:
                log_message(f"Failed to extract brand: {str(e)}", "error")

            # Extract structured data if available
            try:
                structured_data = page.evaluate('''() => {
                    const elements = document.querySelectorAll('script[type="application/ld+json"]');
                    return Array.from(elements).map(el => JSON.parse(el.textContent));
                }''')
                
                if structured_data:
                    for item in structured_data:
                        if isinstance(item, dict):
                            if item.get('@type') == 'Product':
                                if not data['product_name'] and item.get('name'):
                                    data['product_name'] = item['name']
                                if not data['description'] and item.get('description'):
                                    data['description'] = item['description']
                                if not data['brand'] and item.get('brand', {}).get('name'):
                                    data['brand'] = item['brand']['name']
                                if not data['regular_price'] and item.get('offers', {}).get('price'):
                                    data['regular_price'] = float(item['offers']['price'])
            except Exception as e:
                log_message(f"Failed to extract structured data: {str(e)}", "error")

            return data
        except Exception as e:
            log_message(f"Error extracting product data: {str(e)}", "error")
            return None

    def crawl(self, urls, max_pages=1, custom_selectors=None):
        """Crawl multiple URLs with optional custom selectors"""
        if custom_selectors:
            self.custom_selectors = custom_selectors
            
        results = {}
        for url in urls:
            try:
                log_message(f"Starting to crawl: {url}")
                page = self.browser.new_page()
                page.goto(url, wait_until='networkidle')
                results[url] = self.extract_product_data(page)
                page.close()
                log_message(f"Successfully crawled: {url}")
            except Exception as e:
                log_message(f"Error crawling {url}: {str(e)}", "error")
                results[url] = None
        
        self.browser.close()
        self.playwright.stop()
        log_message("Crawler finished and cleaned up")
        return results

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/logs')
def logs():
    def generate():
        while True:
            try:
                log_entry = log_queue.get(timeout=1)
                yield f"data: {json.dumps(log_entry)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), 'message': 'heartbeat', 'level': 'debug'})}\n\n"
            sleep(0.1)  # Prevent CPU overload

    return Response(generate(), mimetype='text/event-stream')

@app.route('/api/crawl', methods=['POST'])
def start_crawl():
    data = request.get_json()
    urls = data.get('urls', [])
    custom_selectors = data.get('selectors', {})
    
    if not urls:
        return jsonify({
            'status': 'error',
            'message': 'No URLs provided'
        }), 400

    # Validate URLs
    valid_urls = []
    for url in urls:
        if validate_url(url):
            valid_urls.append(url)
        else:
            log_message(f"Invalid URL provided: {url}", "error")

    if not valid_urls:
        return jsonify({
            'status': 'error',
            'message': 'No valid URLs provided'
        }), 400

    # Generate a job ID
    job_id = str(uuid.uuid4())
    
    def crawl_task():
        try:
            store_job_status(job_id, 'running')
            crawler = EcommerceCrawler(custom_selectors)
            results = crawler.crawl(valid_urls)
            store_job_status(job_id, 'completed', results=results)
        except Exception as e:
            log_message(f"Crawling job {job_id} failed: {str(e)}", "error")
            store_job_status(job_id, 'failed', error=str(e))

    # Start crawling in a background thread
    thread = threading.Thread(target=crawl_task)
    thread.daemon = True
    thread.start()

    return jsonify({
        'status': 'started',
        'job_id': job_id
    })

@app.route('/api/schedule', methods=['POST'])
def schedule_crawl():
    data = request.get_json()
    frequency = data.get('frequency')
    urls = data.get('urls', [])
    
    if not frequency or not urls:
        return jsonify({
            'status': 'error',
            'message': 'Frequency and URLs are required'
        }), 400
    
    job_id = datetime.now().strftime('%Y%m%d%H%M%S')
    
    # Schedule the job based on frequency
    if frequency == 'hourly':
        scheduler.add_job(
            func=lambda: EcommerceCrawler().crawl(urls),
            trigger='interval',
            hours=1,
            id=job_id
        )
    elif frequency == 'daily':
        scheduler.add_job(
            func=lambda: EcommerceCrawler().crawl(urls),
            trigger='interval',
            days=1,
            id=job_id
        )
    elif frequency == 'weekly':
        scheduler.add_job(
            func=lambda: EcommerceCrawler().crawl(urls),
            trigger='interval',
            weeks=1,
            id=job_id
        )
    
    crawling_jobs[job_id] = {
        'frequency': frequency,
        'urls': urls,
        'status': 'scheduled'
    }
    
    return jsonify({
        'status': 'success',
        'job_id': job_id,
        'message': f'Scheduled crawling job with {frequency} frequency'
    })

@app.route('/api/status/<job_id>', methods=['GET'])
def get_status(job_id):
    """Get the status of a crawling job"""
    try:
        job_data = get_job_status(job_id)
        if job_data:
            return jsonify(job_data)
        
        return jsonify({
            'status': 'error',
            'message': 'Job ID not found'
        }), 404
    except Exception as e:
        log_message(f"Error checking job status: {str(e)}", "error")
        return jsonify({
            'status': 'error',
            'message': f'Error checking job status: {str(e)}'
        }), 500

@app.route('/api/results/<job_id>', methods=['GET'])
def get_results(job_id):
    """Get the results of a completed crawling job"""
    try:
        job_data = get_job_status(job_id)
        if not job_data:
            return jsonify({
                'status': 'error',
                'message': 'Results not found for this job ID'
            }), 404
            
        if job_data['status'] == 'completed':
            return jsonify({
                'status': 'success',
                'results': job_data['results']
            })
        else:
            return jsonify({
                'status': 'error',
                'message': job_data.get('error', 'Unknown error occurred')
            }), 500
    except Exception as e:
        log_message(f"Error retrieving results: {str(e)}", "error")
        return jsonify({
            'status': 'error',
            'message': f'Error retrieving results: {str(e)}'
        }), 500

@app.route('/api/export/<job_id>', methods=['GET'])
def export_data(job_id):
    format_type = request.args.get('format', 'json')
    
    try:
        job_data = get_job_status(job_id)
        if not job_data or 'results' not in job_data:
            return jsonify({
                'error': 'No data found for the specified job ID'
            }), 404

        results = job_data['results']
        
        if format_type == 'json':
            return jsonify(results)
        
        elif format_type == 'csv':
            import csv
            from io import StringIO
            
            # Create CSV file in memory
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=[
                'url', 'product_name', 'description', 'regular_price', 'promotional_price',
                'brand', 'sku', 'stock_status', 'categories', 'images'
            ])
            
            writer.writeheader()
            for url, data in results.items():
                row = {
                    'url': url,
                    'product_name': data.get('product_name', ''),
                    'description': data.get('description', ''),
                    'regular_price': data.get('regular_price', ''),
                    'promotional_price': data.get('promotional_price', ''),
                    'brand': data.get('brand', ''),
                    'sku': data.get('sku', ''),
                    'stock_status': data.get('stock_status', ''),
                    'categories': ' > '.join(data.get('categories', [])),
                    'images': ', '.join(data.get('images', []))
                }
                writer.writerow(row)
            
            # Create the response
            output.seek(0)
            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': f'attachment;filename=products_{job_id}.csv'}
            )
        
        elif format_type == 'markdown':
            markdown = "# Crawled Products Report\n\n"
            
            for url, data in results.items():
                markdown += f"## {data.get('product_name', 'Unnamed Product')}\n\n"
                markdown += f"**URL:** {url}\n\n"
                
                if data.get('description'):
                    markdown += f"**Description:** {data['description']}\n\n"
                
                if data.get('regular_price'):
                    markdown += f"**Price:** {data['regular_price']}"
                    if data.get('promotional_price'):
                        markdown += f" ~~{data['promotional_price']}~~"
                    markdown += "\n\n"
                
                if data.get('brand'):
                    markdown += f"**Brand:** {data['brand']}\n\n"
                
                if data.get('sku'):
                    markdown += f"**SKU:** {data['sku']}\n\n"
                
                if data.get('stock_status'):
                    markdown += f"**Stock Status:** {data['stock_status']}\n\n"
                
                if data.get('categories'):
                    markdown += f"**Categories:** {' > '.join(data['categories'])}\n\n"
                
                if data.get('images'):
                    markdown += "**Images:**\n\n"
                    for img in data['images']:
                        markdown += f"![Product Image]({img})\n\n"
                
                markdown += "---\n\n"
            
            return Response(
                markdown,
                mimetype='text/markdown',
                headers={'Content-Disposition': f'attachment;filename=products_{job_id}.md'}
            )
        
        else:
            return jsonify({
                'error': 'Invalid export format'
            }), 400
            
    except Exception as e:
        app.logger.error(f"Error exporting data: {str(e)}")
        return jsonify({
            'error': f'Error exporting data: {str(e)}'
        }), 500

@app.route('/preview_page', methods=['POST'])
def preview_page():
    """Get the HTML content of a page for the selector helper"""
    url = request.json.get('url')
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto(url)
            
            # Wait for the page to load
            page.wait_for_load_state('networkidle')
            
            # Get the HTML content
            html = page.content()
            
            # Clean up
            context.close()
            browser.close()
            
            return html
    except Exception as e:
        app.logger.error(f"Error previewing page: {str(e)}")
        return jsonify({'error': str(e)}), 500

def validate_url(url):
    """Validate if the URL is properly formatted"""
    url_pattern = re.compile(
        r'^https?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,6}\.?|'  # domain...
        r'localhost|'  # localhost...
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # optional port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    return url_pattern.match(url) is not None

if __name__ == '__main__':
    # Only for development
    app.run(debug=False, host='0.0.0.0', port=8080)
