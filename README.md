# Cloud-Based AWS EC2 Server Monitoring and Alerting Dashboard

A simple web dashboard that watches the health of an Amazon EC2 server and
sends an email when something goes wrong.

## Features
- Live charts for CPU, network in, network out and status check
- Auto refresh every 30 seconds, with a 1 to 24 hour time range
- Email alerts through Amazon SNS when CPU is 80% or more or a status check fails
- Recovery email when the server is back to normal
- Alert history table on the dashboard
- Secure access with AWS IAM (no keys in the code)

## Tech stack
Python, Flask, Boto3, HTML, CSS, JavaScript, Chart.js,
Amazon EC2, CloudWatch, SNS and IAM

## How to run
1. Install the requirements: `pip install -r requirements.txt`
2. Configure AWS: `aws configure`
3. Copy `.env.example` to `.env` and fill in your region, instance ID and SNS topic ARN
4. Start the app: `python app.py`
5. Open http://localhost:5000

## Project info
Second year project report available on request.
