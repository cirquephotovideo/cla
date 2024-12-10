let currentJobId = null;

document.getElementById('crawlForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const urlsText = document.getElementById('urls').value;
    const urls = urlsText.split('\n').filter(url => url.trim());
    const numPages = document.getElementById('numPages').value;
    
    if (urls.length === 0) {
        alert('Please enter at least one URL');
        return;
    }
    
    try {
        const response = await fetch('/api/crawl', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                urls: urls,
                num_pages: parseInt(numPages)
            })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            currentJobId = data.job_id;
            updateStatus();
            if (data.invalid_urls.length > 0) {
                alert(`Warning: The following URLs are invalid:\n${data.invalid_urls.join('\n')}`);
            }
        } else {
            alert(`Error: ${data.message}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while starting the crawler');
    }
});

document.getElementById('scheduleForm').addEventListener('submit', async (e) => {
    e.preventDefault();
    
    const frequency = document.getElementById('frequency').value;
    const urlsText = document.getElementById('urls').value;
    const urls = urlsText.split('\n').filter(url => url.trim());
    
    if (urls.length === 0) {
        alert('Please enter at least one URL');
        return;
    }
    
    try {
        const response = await fetch('/api/schedule', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                frequency: frequency,
                urls: urls
            })
        });
        
        const data = await response.json();
        
        if (data.status === 'success') {
            alert(`Successfully scheduled crawling job with ${frequency} frequency`);
        } else {
            alert(`Error: ${data.message}`);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while scheduling the crawler');
    }
});

async function updateStatus() {
    if (!currentJobId) return;
    
    try {
        const response = await fetch(`/api/status/${currentJobId}`);
        const data = await response.json();
        
        if (data.status === 'completed') {
            updateStatistics(data.results);
            displayProducts(data.results);
        } else if (data.status === 'error') {
            alert(`Error: ${data.message}`);
        }
        
        // Continue updating status if job is still running
        if (data.status !== 'completed' && data.status !== 'error') {
            setTimeout(updateStatus, 5000);
        }
    } catch (error) {
        console.error('Error:', error);
    }
}

function updateStatistics(results) {
    const totalUrls = Object.keys(results).length;
    let successful = 0;
    let failed = 0;
    
    for (const url in results) {
        if (results[url] && results[url].length > 0) {
            successful++;
        } else {
            failed++;
        }
    }
    
    document.getElementById('totalUrls').textContent = totalUrls;
    document.getElementById('successfulUrls').textContent = successful;
    document.getElementById('failedUrls').textContent = failed;
}

function displayProducts(results) {
    const productsList = document.getElementById('productsList');
    productsList.innerHTML = '';
    
    for (const url in results) {
        const products = results[url];
        if (!products || products.length === 0) continue;
        
        products.forEach(product => {
            const productCard = document.createElement('div');
            productCard.className = 'col-md-4 mb-4';
            productCard.innerHTML = `
                <div class="card h-100">
                    <img src="${product.images[0] || ''}" class="card-img-top product-image" alt="${product.product_name}">
                    <div class="card-body">
                        <h5 class="card-title">${product.product_name}</h5>
                        <p class="card-text">${product.description || ''}</p>
                        <p class="card-text">
                            <strong>Price:</strong> ${product.regular_price || 'N/A'}<br>
                            <strong>Stock:</strong> ${product.stock_status || 'N/A'}<br>
                            <strong>SKU:</strong> ${product.sku || 'N/A'}
                        </p>
                    </div>
                </div>
            `;
            productsList.appendChild(productCard);
        });
    }
}

async function exportData(format) {
    if (!currentJobId) {
        alert('No data available for export');
        return;
    }
    
    try {
        const response = await fetch(`/api/export/${currentJobId}?format=${format}`);
        
        if (format === 'json') {
            const data = await response.json();
            downloadFile(`crawl-data-${currentJobId}.json`, JSON.stringify(data, null, 2));
        } else if (format === 'markdown') {
            const text = await response.text();
            downloadFile(`crawl-data-${currentJobId}.md`, text);
        }
    } catch (error) {
        console.error('Error:', error);
        alert('An error occurred while exporting data');
    }
}

function downloadFile(filename, content) {
    const element = document.createElement('a');
    element.setAttribute('href', 'data:text/plain;charset=utf-8,' + encodeURIComponent(content));
    element.setAttribute('download', filename);
    element.style.display = 'none';
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
}
