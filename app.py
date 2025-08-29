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
        
        # Generate HTML for comments section
        comment_html_parts = []
        if comments:
            for comment in comments:
                # Ensure author exists and is not empty before accessing index 0
                author_initial = '?'
                if comment.get('author'):
                    # Handle potential empty author string
                    if comment['author']:
                        author_initial = comment['author'][0].upper()
                    else:
                        author_initial = '?' # Explicitly handle empty string case
                
                # Add the direct link to the comment
                comment_link = comment.get('link', video_url)
                timestamp_links_html = ''.join(
                    f'<div class="meta-item"><a href="{ts["link"]}" target="_blank" class="timestamp-link">{ts["text"]}</a></div>'
                    for ts in comment.get('timestamps', [])
                )

                comment_html_parts.append(f"""
                <div class="comment">
                    <div class="comment-header">
                        <div class="author-avatar">{author_initial}</div>
                        <div class="author-name">{comment.get('author', 'Unknown')}</div>
                    </div>
                    <div class="comment-body">
                        <div class="comment-text">{comment.get('text', '')}</div>
                        <div class="comment-meta">
                            <div class="meta-item">
                                <span class="meta-icon"><i>&#128337;</i></span>
                                {comment.get('time', 'Unknown')}
                            </div>
                            <div class="meta-item">
                                <span class="meta-icon"><i>&#128077;</i></span>
                                <span class="likes-count">{comment.get('likes', 0)}</span>
                            </div>
                            {timestamp_links_html}
                            <div class="meta-item">
                                <a href="{comment_link}" target="_blank" class="comment-link">View Comment</a>
                            </div>
                        </div>
                    </div>
                </div>
                """)
            comments_html = ''.join(comment_html_parts)
        else:
            comments_html = '''
            <div class="empty-state">
                <div class="empty-state-icon">&#128269;</div>
                <p>No matching comments found for your search criteria.</p>
            </div>
            '''

        # Create HTML version of email
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>YouTube Comment Results</title>
            <style>
                /* Base styles */
                body {{
                    font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    background-color: #f9f9f9;
                    margin: 0;
                    padding: 0;
                }}
                
                .container {{
                    max-width: 700px;
                    margin: 0 auto;
                    background-color: #ffffff;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                }}
                
                /* Header */
                .header {{
                    background-color: #FF0000;
                    color: white;
                    padding: 25px;
                    text-align: center;
                }}
                
                .header h1 {{
                    margin: 0;
                    font-size: 24px;
                    font-weight: 600;
                }}
                
                /* Content */
                .content {{
                    padding: 25px;
                }}
                
                .summary {{
                    background-color: #f5f5f5;
                    border-radius: 6px;
                    padding: 15px;
                    margin-bottom: 25px;
                    border-left: 4px solid #FF0000;
                }}
                
                .summary-item {{
                    margin-bottom: 10px;
                }}
                
                .summary-label {{
                    font-weight: 600;
                    color: #555;
                    margin-right: 5px;
                }}
                
                .video-link {{
                    color: #0066cc;
                    text-decoration: none;
                    word-break: break-all;
                }}
                
                .video-link:hover, .comment-link:hover {{
                    text-decoration: underline;
                }}
                
                .comment-link {{
                    color: #0066cc;
                    text-decoration: none;
                    font-weight: 500;
                }}
                
                .phrase-tag {{
                    display: inline-block;
                    background-color: #f0f0f0;
                    border: 1px solid #ddd;
                    border-radius: 16px;
                    padding: 4px 12px;
                    margin: 3px;
                    font-size: 14px;
                }}
                
                .comments-header {{
                    font-size: 20px;
                    color: #333;
                    margin-top: 30px;
                    margin-bottom: 15px;
                    border-bottom: 2px solid #eee;
                    padding-bottom: 10px;
                }}
                
                /* Comment styles */
                .comment {{
                    margin-bottom: 20px;
                    background-color: #fff;
                    border-radius: 8px;
                    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
                    overflow: hidden;
                    transition: transform 0.2s;
                }}
                
                .comment:hover {{
                    transform: translateY(-2px);
                    box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
                }}
                
                .comment-header {{
                    display: flex;
                    align-items: center;
                    background-color: #f8f8f8;
                    padding: 12px 15px;
                    border-bottom: 1px solid #eee;
                }}
                
                .author-avatar {{
                    width: 36px;
                    height: 36px;
                    background-color: #FF0000;
                    border-radius: 50%;
                    color: white;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-weight: bold;
                    margin-right: 12px;
                }}
                
                .author-name {{
                    font-weight: 600;
                    color: #333;
                    flex-grow: 1;
                }}
                
                .comment-body {{
                    padding: 15px;
                    background-color: white;
                }}
                
                .comment-text {{
                    margin-bottom: 15px;
                    white-space: pre-wrap;
                }}
                
                .comment-meta {{
                    display: flex;
                    color: #888;
                    font-size: 13px;
                    border-top: 1px solid #f0f0f0;
                    padding-top: 10px;
                }}
                
                .meta-item {{
                    display: flex;
                    align-items: center;
                    margin-right: 15px;
                }}
                
                .meta-icon {{
                    margin-right: 5px;
                    font-size: 14px;
                }}
                
                .likes-count {{
                    color: #333;
                    font-weight: 500;
                }}
                
                /* Footer */
                .footer {{
                    text-align: center;
                    padding: 15px;
                    color: #888;
                    font-size: 13px;
                    background-color: #f9f9f9;
                    border-top: 1px solid #eee;
                }}
                
                /* Responsive */
                @media (max-width: 600px) {{
                    .header {{
                        padding: 15px;
                    }}
                    
                    .content {{
                        padding: 15px;
                    }}
                    
                    .comment-header,
                    .comment-body {{
                        padding: 10px;
                    }}
                }}
                
                /* Empty state */
                .empty-state {{
                    text-align: center;
                    padding: 40px 20px;
                    color: #888;
                }}
                
                .empty-state-icon {{
                    font-size: 48px;
                    margin-bottom: 15px;
                    color: #ddd;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>YouTube Comment Search Results</h1>
                </div>
                
                <div class="content">
                    <div class="summary">
                        <div class="summary-item">
                            <span class="summary-label">Video URL:</span>
                            <a href="{video_url}" class="video-link" target="_blank">{video_url}</a>
                        </div>
                        
                        <div class="summary-item">
                            <span class="summary-label">Search Phrases:</span>
                            <div style="display: inline-block;">
                                {''.join([f'<span class="phrase-tag">{phrase}</span>' for phrase in phrases])}
                            </div>
                        </div>
                        
                        <div class="summary-item">
                            <span class="summary-label">Matching Comments:</span>
                            <strong>{len(comments)}</strong>
                        </div>
                    </div>
                    
                    <div class="comments-header">Results</div>
                    
                    {comments_html}
                </div>
                
                <div class="footer">
                    This report was generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}
                </div>
            </div>
        </body>
        </html>
        """
        
        # Attach HTML content
        part = MIMEText(html, "html")
        msg.attach(part)
        
        # Create secure connection with Gmail's SMTP server
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