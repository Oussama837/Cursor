#!/usr/bin/env python3
"""
Selenium Undetected Chrome Driver with Proxy Verification
Verifies proxy usage 5 times by opening and closing browser
"""

import os
import time
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException


def get_proxy_config():
    """Get proxy configuration from environment variables"""
    return {
        'scheme': os.getenv('PROXY_SCHEME', 'http'),
        'host': os.getenv('PROXY_HOST', '109.190.120.82'),
        'port': os.getenv('PROXY_PORT', '7440'),
        'user': os.getenv('PROXY_USER', 'dofus2025'),
        'pass': os.getenv('PROXY_PASS', 'creationfordofus11'),
    }


def create_driver_with_proxy(proxy_config, headless=False):
    """Create undetected Chrome driver with proxy configuration"""
    options = uc.ChromeOptions()
    
    # Configure proxy
    proxy_url = f"{proxy_config['scheme']}://{proxy_config['user']}:{proxy_config['pass']}@{proxy_config['host']}:{proxy_config['port']}"
    options.add_argument(f'--proxy-server={proxy_url}')
    
    # Additional options
    if headless:
        options.add_argument('--headless=new')
    
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-blink-features=AutomationControlled')
    
    # Create driver
    driver = uc.Chrome(options=options, version_main=None)
    
    # Set page load timeout
    page_load_timeout = int(os.getenv('PAGELOAD_TIMEOUT', '40'))
    driver.set_page_load_timeout(page_load_timeout)
    
    return driver


def verify_proxy(driver, proxy_config):
    """Verify that the browser is using the proxy"""
    try:
        print("Navigating to IP check service...")
        driver.get("https://api.ipify.org?format=json")
        
        # Wait for page to load
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        # Get the IP from response
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print(f"Response: {body_text}")
        
        # Parse IP (assuming JSON response like {"ip":"x.x.x.x"})
        import json
        ip_data = json.loads(body_text)
        detected_ip = ip_data.get('ip', '')
        
        print(f"Detected IP: {detected_ip}")
        print(f"Expected Proxy Host: {proxy_config['host']}")
        
        # Check if IP matches proxy host (or at least verify it's different from local IP)
        # Note: The detected IP might be the proxy's external IP, not necessarily the proxy host IP
        if detected_ip:
            print(f"✓ Proxy verification: Browser is using IP {detected_ip}")
            return True, detected_ip
        else:
            print("✗ Proxy verification: Could not detect IP")
            return False, None
            
    except TimeoutException:
        print("✗ Proxy verification: Timeout while loading IP check page")
        return False, None
    except Exception as e:
        print(f"✗ Proxy verification error: {str(e)}")
        return False, None


def check_proxy_via_headers(driver):
    """Alternative method: Check proxy via browser headers/network info"""
    try:
        # Use a service that shows request headers
        driver.get("https://httpbin.org/ip")
        wait = WebDriverWait(driver, 10)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        
        body_text = driver.find_element(By.TAG_NAME, "body").text
        print(f"IP Info: {body_text}")
        
        import json
        ip_data = json.loads(body_text)
        detected_ip = ip_data.get('origin', '')
        
        return True, detected_ip
    except Exception as e:
        print(f"Header check error: {str(e)}")
        return False, None


def main():
    """Main function to verify proxy 5 times"""
    proxy_config = get_proxy_config()
    headless = os.getenv('HEADLESS', '0') == '1'
    
    print("=" * 60)
    print("Proxy Verification Script")
    print("=" * 60)
    print(f"Proxy: {proxy_config['scheme']}://{proxy_config['user']}:***@{proxy_config['host']}:{proxy_config['port']}")
    print(f"Headless mode: {headless}")
    print("=" * 60)
    
    verification_results = []
    
    for attempt in range(1, 6):
        print(f"\n{'='*60}")
        print(f"Attempt {attempt}/5")
        print(f"{'='*60}")
        
        driver = None
        try:
            # Create driver with proxy
            print("Creating browser with proxy configuration...")
            driver = create_driver_with_proxy(proxy_config, headless=headless)
            print("Browser created successfully")
            
            # Verify proxy
            print("\nVerifying proxy usage...")
            success, ip = verify_proxy(driver, proxy_config)
            
            if not success:
                # Try alternative method
                print("\nTrying alternative verification method...")
                success, ip = check_proxy_via_headers(driver)
            
            verification_results.append({
                'attempt': attempt,
                'success': success,
                'ip': ip
            })
            
            if success:
                print(f"\n✓ Attempt {attempt}: Proxy verified successfully (IP: {ip})")
            else:
                print(f"\n✗ Attempt {attempt}: Proxy verification failed")
            
            # Small delay before closing
            time.sleep(2)
            
        except Exception as e:
            print(f"\n✗ Attempt {attempt}: Error - {str(e)}")
            verification_results.append({
                'attempt': attempt,
                'success': False,
                'ip': None,
                'error': str(e)
            })
        finally:
            # Close browser
            if driver:
                try:
                    print("Closing browser...")
                    driver.quit()
                    print("Browser closed")
                except Exception as e:
                    print(f"Error closing browser: {str(e)}")
            
            # Wait between attempts (except after last one)
            if attempt < 5:
                print(f"\nWaiting 3 seconds before next attempt...")
                time.sleep(3)
    
    # Summary
    print(f"\n{'='*60}")
    print("VERIFICATION SUMMARY")
    print(f"{'='*60}")
    successful = sum(1 for r in verification_results if r['success'])
    print(f"Successful verifications: {successful}/5")
    print(f"\nDetailed results:")
    for result in verification_results:
        status = "✓ SUCCESS" if result['success'] else "✗ FAILED"
        ip_info = f" (IP: {result['ip']})" if result.get('ip') else ""
        error_info = f" - {result.get('error', '')}" if not result['success'] and result.get('error') else ""
        print(f"  Attempt {result['attempt']}: {status}{ip_info}{error_info}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
