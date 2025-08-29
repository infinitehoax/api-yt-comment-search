import os
import pytest

os.environ.setdefault("GMAIL_USER", "test")
os.environ.setdefault("GMAIL_APP_PASSWORD", "test")

from app import extract_timestamps


def test_extract_timestamps_handles_multiple_formats():
    video_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    text = "Wow check 1:23 and later 10:05:30 in the video!"
    result = extract_timestamps(text, video_url)
    assert result[0]['text'] == '1:23'
    assert result[0]['seconds'] == 83
    assert result[0]['link'].endswith('t=83s')
    assert result[1]['text'] == '10:05:30'
    assert result[1]['seconds'] == 36330
    assert result[1]['link'].endswith('t=36330s')
