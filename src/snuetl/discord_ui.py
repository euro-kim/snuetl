from __future__ import annotations

VIDEO_PAGE_SIZE = 25


def video_page(videos: list[dict[str, object]], page: int) -> list[dict[str, object]]:
    if page < 0:
        raise ValueError("video page cannot be negative")
    start = page * VIDEO_PAGE_SIZE
    return videos[start : start + VIDEO_PAGE_SIZE]


def video_page_count(videos: list[dict[str, object]]) -> int:
    return max(1, (len(videos) + VIDEO_PAGE_SIZE - 1) // VIDEO_PAGE_SIZE)


def merge_video_page_selection(
    selected: set[str],
    page_items: list[dict[str, object]],
    page_selection: list[str],
) -> None:
    page_ids = {str(item["video_id"]) for item in page_items}
    values = set(page_selection)
    if not values <= page_ids:
        raise ValueError("selection contains a video outside the current page")
    selected.difference_update(page_ids)
    selected.update(values)
