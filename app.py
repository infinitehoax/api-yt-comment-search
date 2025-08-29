import os
import json
import uuid
import time
import threading
import logging
import smtplib
import ssl
import re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from queue import Queue
from threading import Lock
from flask import Flask, request, jsonify

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Try to import the YouTube comment downloader
try:
    from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_RECENT
except ImportError:
    logger.error("youtube_comment_downloader is required. Install using: pip install youtube-comment-downloader")
    exit(1)

# Flask application setup
app = Flask(__name__)

# Configuration from environment variables
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")

if not GMAIL_USER or not GMAIL_APP_PASSWORD:
    logger.error("Gmail credentials not found in environment variables")
    exit(1)

# Global variables
request_queue = Queue()
request_status = {}
queue_lock = Lock()
QUEUE_FILE = "request_queue.json"

# Load existing queue from file on startup
def load_queue_from_file():
    logger.info("Attempting to load queue from file...")
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, 'r') as f:
                saved_requests = json.load(f)
                
                # Add saved requests to the queue and status dictionary
                for req_id, req_data in saved_requests.items():
                    if req_data['status'] == 'pending':
                        request_queue.put((req_id, req_data))
                    request_status[req_id] = req_data
                    
            logger.info(f"Finished loading queue. {len(saved_requests)} requests loaded from {QUEUE_FILE}.")
    except Exception as e:
        logger.exception(f"Error loading queue from file {QUEUE_FILE}: {e}")

# Save queue to file
# Note: This function assumes queue_lock is already held by the caller
def save_queue_to_file():
    try:
        # The lock should be acquired by the calling context
        with open(QUEUE_FILE, 'w') as f:
            json.dump(request_status, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving queue to file: {e}")

# Extract timestamp references from comment text
TIMESTAMP_PATTERN = re.compile(r"\b(\d{1,2}:\d{2}(?::\d{2})?)\b")


def _timestamp_to_seconds(ts: str) -> int:
    """Convert a timestamp string (H:MM:SS or MM:SS) into total seconds."""
    parts = [int(p) for p in ts.split(":")]
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def extract_timestamps(text: str, video_url: str):
    """Return timestamps referenced in text with direct video links."""
    timestamps = []
    for match in TIMESTAMP_PATTERN.findall(text):
        seconds = _timestamp_to_seconds(match)
        delimiter = "&" if "?" in video_url else "?"
        link = f"{video_url}{delimiter}t={seconds}s"
        timestamps.append({"text": match, "seconds": seconds, "link": link})
    return timestamps

# Process YouTube comments - improved version based on the test script
def get_filtered_comments(video_url, phrases):
    logger.info(f"Attempting to get comments for {video_url} with phrases: {phrases}")
    try:
        logger.info("Initializing YoutubeCommentDownloader...")
        downloader = YoutubeCommentDownloader()
        
        logger.info(f"Calling get_comments_from_url for {video_url} with SORT_BY_RECENT...")
        comments_iterator = downloader.get_comments_from_url(video_url, sort_by=SORT_BY_RECENT)
        
        filtered_comments = []
        comment_count = 0
        
        for comment in comments_iterator:
            comment_count += 1
            if comment_count % 100 == 0:
                logger.info(f"Processed {comment_count} comments so far...")
            
            comment_text_lower = comment.get('text', '').lower()
            
            # Check if all phrases are in the comment (logical AND)
            if all(phrase.lower() in comment_text_lower for phrase in phrases):
                # Extract the comment ID to create a direct link
                comment_id = comment.get('cid', '')
                comment_link = f"{video_url}&lc={comment_id}" if comment_id else video_url

                filtered_comments.append({
                    'text': comment.get('text', ''),
                    'author': comment.get('author', 'Unknown'),
                    'time': comment.get('time', 'Unknown'),
                    'likes': comment.get('votes', 0),
                    'link': comment_link,
                    'timestamps': extract_timestamps(comment.get('text', ''), video_url)
                })
        
        logger.info(f"Successfully processed {comment_count} comments and found {len(filtered_comments)} matching comments for {video_url}")
        return filtered_comments
    except Exception as e:
        logger.exception(f"Error getting comments for {video_url}: {e}")
        return []

# Send email with results
def send_email_report(to_email, video_url, phrases, comments):
    logger.info(f"Attempting to send email report to {to_email} for {video_url}")
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"YouTube Comment Search Results: {', '.join(phrases)}"
        msg["From"] = GMAIL_USER
        msg["To"] = to_email

        template = app.jinja_env.get_template("email_report.html")
        html = template.render(
            video_url=video_url,
            phrases=phrases,
            comments=comments,
            generated_on=datetime.now().strftime('%B %d, %Y at %I:%M %p'),
        )

        part = MIMEText(html, "html")
        msg.attach(part)

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            server.sendmail(
                GMAIL_USER, to_email, msg.as_string()
            )

        logger.info(f"Email sent successfully to {to_email} for {video_url}")
        return True
    except Exception as e:
        logger.exception(f"Error sending email to {to_email} for {video_url}: {e}")
        return False

# Worker thread to process requests
def process_queue():
    while True:
        try:
            if not request_queue.empty():
                request_id, request_data = request_queue.get()
                
                try:
                    logger.info(f"Processing request {request_id}: {request_data['video_url']}")
                    
                    # Update status to processing
                    with queue_lock:
                        request_status[request_id]['status'] = 'processing'
                        save_queue_to_file()
                    
                    logger.info(f"[{request_id}] Calling get_filtered_comments...")
                    # Get and filter comments
                    comments = get_filtered_comments(
                        request_data['video_url'],
                        request_data['phrases']
                    )
                    logger.info(f"[{request_id}] get_filtered_comments returned {len(comments)} comments.")
                    
                    logger.info(f"[{request_id}] Calling send_email_report...")
                    # Send email with results
                    email_sent = send_email_report(
                        request_data['email'],
                        request_data['video_url'],
                        request_data['phrases'],
                        comments
                    )
                    logger.info(f"[{request_id}] send_email_report returned: {email_sent}")
                    
                    # Update status to completed
                    with queue_lock:
                        request_status[request_id]['status'] = 'completed'
                        request_status[request_id]['completion_time'] = datetime.now().isoformat()
                        request_status[request_id]['result'] = {
                            'comment_count': len(comments),
                            'email_sent': email_sent
                        }
                        save_queue_to_file()
                    
                    logger.info(f"Request {request_id} completed. Found {len(comments)} matching comments.")
                
                except Exception as e:
                    logger.exception(f"Critical error processing request {request_id}: {e}")
                    
                    # Update status to failed
                    with queue_lock:
                        request_status[request_id]['status'] = 'failed'
                        request_status[request_id]['error'] = str(e)
                        save_queue_to_file()
                
                # Mark task as done
                request_queue.task_done()
            else:
                # Sleep to prevent CPU hogging
                time.sleep(1)
        except Exception as e:
            logger.error(f"Error in queue processor: {e}")
            time.sleep(5)  # Wait a bit longer if there's an error

# API endpoint to submit a request
@app.route('/api/submit', methods=['POST'])
def submit_request():
    try:
        data = request.json
        
        # Validate request data
        required_fields = ['video_url', 'phrases', 'email']
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing required field: {field}"}), 400
        
        # Validate video URL (basic check)
        if 'youtube.com/watch' not in data['video_url'] and 'youtu.be/' not in data['video_url']:
            return jsonify({"error": "Invalid YouTube URL"}), 400
        
        # Validate phrases (must be a list)
        if not isinstance(data['phrases'], list) or len(data['phrases']) == 0:
            return jsonify({"error": "Phrases must be a non-empty list"}), 400
        
        # Generate a unique ID for this request
        request_id = str(uuid.uuid4())
        
        # Create request data structure
        request_data = {
            'video_url': data['video_url'],
            'phrases': data['phrases'],
            'email': data['email'],
            'status': 'pending',
            'submission_time': datetime.now().isoformat()
        }
        
        # Add to queue and status dictionary
        with queue_lock:
            request_status[request_id] = request_data
            request_queue.put((request_id, request_data))
            save_queue_to_file()
        
        logger.info(f"Request {request_id} submitted: {data['video_url']}")
        
        return jsonify({
            "request_id": request_id,
            "status": "pending",
            "message": "Request submitted successfully"
        })
        
    except Exception as e:
        logger.error(f"Error submitting request: {e}")
        return jsonify({"error": str(e)}), 500

# API endpoint to check request status
@app.route('/api/status/<request_id>', methods=['GET'])
def check_status(request_id):
    if request_id in request_status:
        return jsonify(request_status[request_id])
    else:
        return jsonify({"error": "Request not found"}), 404

# Add a simple console-based testing function
def test_comment_extraction(video_url, phrases):
    """
    Utility function to test comment extraction directly
    """
    print(f"Searching for comments that include *all* of these phrases: {phrases}")
    print(f"In the comments from: {video_url}\n")
    
    comments = get_filtered_comments(video_url, phrases)
    
    if comments:
        print(f"Found {len(comments)} comment(s) containing all the specified phrases:\n")
        for i, comment in enumerate(comments, start=1):
            print(f"=== Match #{i} ===")
            print(f"Comment by   : {comment['author']}")
            print(f"Comment text : {comment['text']}")
            print(f"Comment link : {comment['link']}\n")
    else:
        print("No comments match all the phrases. Try again with different phrases or a different video.")
    
    return comments

# Initialize the application
if __name__ == "__main__":
    # Check if we're in testing mode
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        # Default test values
        test_video_url = "https://www.youtube.com/watch?v=2ZcedEdh_RI"
        test_phrases = ["expensive"]
        
        # Override with command line arguments if provided
        if len(sys.argv) > 2:
            test_video_url = sys.argv[2]
        if len(sys.argv) > 3:
            test_phrases = sys.argv[3].split(',')
        
        # Run test
        test_comment_extraction(test_video_url, test_phrases)
    else:
        # Normal server operation
        # Load any existing queue
        load_queue_from_file()
        
        # Start the worker thread
        worker_thread = threading.Thread(target=process_queue, daemon=True)
        worker_thread.start()
        logger.info("Worker thread started.")
        
        # Run the Flask application
        app.run(host='0.0.0.0', port=5000, debug=False)