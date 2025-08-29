# YouTube Comment Retriever and Notifier

This application retrieves YouTube comments that contain specific phrases and sends the results via email using Gmail SMTP. Comments that mention video timestamps (e.g., `1:23` or `10:05:30`) are now detected and returned with direct links to those moments in the video.

## Installation

1. Clone or download the repository
2. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

## Configuration

Set the following environment variables before running the application:

```bash
export GMAIL_USER="your.email@gmail.com"
export GMAIL_APP_PASSWORD="your-app-password"
```

**Important**: You must use an App Password with Gmail, not your regular password. To create an App Password:
1. Enable 2-Step Verification on your Google Account
2. Go to your Google Account > Security > App passwords
3. Generate a new app password for "Mail" and "Other (Custom name)"
4. Use this generated password as your `GMAIL_APP_PASSWORD`

## Running the Application

```bash
python app.py
```

The server will start on port 5000.

## API Endpoints

### 1. Submit a Request

**Endpoint**: `POST /api/submit`

**Request Body**:
```json
{
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "phrases": ["phrase1", "phrase2"],
  "email": "recipient@example.com"
}
```

**Response**:
```json
{
  "request_id": "uuid-string",
  "status": "pending",
  "message": "Request submitted successfully"
}
```

### 2. Check Request Status

**Endpoint**: `GET /api/status/<request_id>`

**Response**:
```json
{
  "video_url": "https://www.youtube.com/watch?v=VIDEO_ID",
  "phrases": ["phrase1", "phrase2"],
  "email": "recipient@example.com",
  "status": "completed",
  "submission_time": "2023-09-20T15:30:45.123456",
  "completion_time": "2023-09-20T15:35:12.654321",
  "result": {
    "comment_count": 15,
    "email_sent": true
  }
}
```

## How It Works

1. The application maintains a queue of comment retrieval requests
2. When a request is submitted, it's added to the queue with a unique ID
3. A background worker processes each request:
   - Downloads comments from the YouTube video
   - Filters comments containing all specified phrases
   - Formats and sends an email with the results
4. Requests are saved to disk to survive application restarts
5. Users can check the status of their request using the request ID

## Error Handling

The application includes robust error handling and logging:
- Failed requests are marked with status "failed" and include an error message
- All actions are logged to both console and a file (app.log)
- The queue system is designed to be resilient to errors in individual requests