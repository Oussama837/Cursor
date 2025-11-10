# Proxy Verification Script

This script uses Selenium with undetected-chromedriver to verify proxy usage by opening and closing the browser 5 times.

## Features

- Uses `undetected-chromedriver` to avoid detection
- Configures HTTP proxy with authentication
- Verifies proxy usage by checking IP address
- Opens and closes browser 5 times
- Provides detailed verification results

## Installation

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Set environment variables (or use the .env.example as reference):
```bash
export PROXY_SCHEME=http
export PROXY_HOST=109.190.120.82
export PROXY_PORT=7440
export PROXY_USER=dofus2025
export PROXY_PASS=creationfordofus11
export HEADLESS=0
export PAGELOAD_TIMEOUT=40
```

Or use a `.env` file with `python-dotenv` (optional).

## Usage

Run the script:
```bash
python proxy_verification.py
```

The script will:
1. Create a Chrome browser instance with proxy configuration
2. Navigate to an IP check service
3. Verify the proxy is being used
4. Close the browser
5. Repeat 4 more times
6. Display a summary of all verification attempts

## Configuration

- `HEADLESS=0`: Set to `1` to run in headless mode
- `PAGELOAD_TIMEOUT=40`: Page load timeout in seconds
- Proxy settings: Configure via `PROXY_SCHEME`, `PROXY_HOST`, `PROXY_PORT`, `PROXY_USER`, `PROXY_PASS`

## Output

The script provides:
- Real-time status for each attempt
- Detected IP address for verification
- Summary of all 5 verification attempts
