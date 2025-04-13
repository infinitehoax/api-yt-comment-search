"""
Usage:
1) pip install youtube-comment-downloader
2) Update VIDEO_URL and PHRASES
3) Run the script

This script collects every comment containing *all* phrases in PHRASES,
and prints the comment text, the author, and a direct URL link to that comment.
this is the test py that actually works
"""

from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_POPULAR, SORT_BY_RECENT

def find_comments_with_all_phrases(video_url, phrases):
    downloader = YoutubeCommentDownloader()

    print(f"Searching for comments that include *all* of these phrases: {phrases}")
    print(f"In the comments from: {video_url}\n")

    # Feel free to switch to SORT_BY_POPULAR if desired
    comments = downloader.get_comments_from_url(video_url, sort_by=SORT_BY_RECENT)

    matches = []

    for comment in comments:
        comment_text_lower = comment['text'].lower()

        # Check if *all* phrases appear in the comment text (logical AND)
        if all(phrase.lower() in comment_text_lower for phrase in phrases):
            # Extract comment info
            author_name = comment['author']
            comment_id = comment['cid']
            comment_link = f"{video_url}&lc={comment_id}"

            matches.append({
                'author': author_name,
                'text': comment['text'],
                'link': comment_link
            })

    # Print results
    if matches:
        print(f"Found {len(matches)} comment(s) containing *all* the specified phrases:\n")
        for i, match in enumerate(matches, start=1):
            print(f"=== Match #{i} ===")
            print(f"Comment by   : {match['author']}")
            print(f"Comment text : {match['text']}")
            print(f"Comment link : {match['link']}\n")
    else:
        print("No comments match all the phrases. Try again with different phrases or a different video.")

if __name__ == "__main__":
    VIDEO_URL = "https://www.youtube.com/watch?v=2ZcedEdh_RI"
    PHRASES = ["expensive"]  # All must appear in the comment

    find_comments_with_all_phrases(VIDEO_URL, PHRASES)
