"""Complete-snapshot pagination for job-board listings."""

import dataclasses
import logging
import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from jobscraper.job_boards.common.http import (
    IncompleteBoardResponse,
    get_with_retries,
    require_job_list,
    validate_ids,
)
from jobscraper.models import normalize_job_id

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, slots=True)
class _PageFormat:
    """Where a provider's paginated response keeps its total, items and IDs."""

    total_key: str
    items_key: str
    id_key: str = "id"

    def total(self, payload: object) -> int:
        if not isinstance(payload, dict):
            raise IncompleteBoardResponse("pagination response is not an object")
        total = payload.get(self.total_key)
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise IncompleteBoardResponse(
                f"missing or invalid {self.total_key!r} total"
            )
        return total

    def items(self, payload: object, cursor: int) -> list[dict]:
        return require_job_list(
            payload, "pagination", str(cursor), key=self.items_key,
            id_key=self.id_key,
        )

    def validate_ids(self, items: list[dict]) -> None:
        validate_ids(items, self.id_key)


def _fetch_twice(
    fetch: Callable[[], list[dict]], source: str, identifier: str, fetch_name: str
) -> list[dict]:
    """Fetch a whole board, and once more if its snapshot is incomplete."""
    try:
        return fetch()
    except IncompleteBoardResponse as exc:
        logger.warning(
            "%s[%s]: incomplete %s (%s), retrying the whole board once",
            source,
            identifier,
            fetch_name,
            exc,
        )
    return fetch()


def paginate_with_retries(
    source: str,
    identifier: str,
    url: str,
    *,
    cursor_param: str,
    page_size_param: str,
    page_size: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    workers: int = 1,
) -> list[dict]:
    """Fetch every page, optionally in parallel after the first."""
    pages = _PageFormat(total_key, items_key, id_key)

    def fetch_page(cursor: int) -> dict:
        response = get_with_retries(
            source, identifier, url,
            params={cursor_param: cursor, page_size_param: page_size},
        )
        return response.json()

    if workers > 1:
        try:
            items = _paginate_in_parallel(fetch_page, pages, workers)
        except Exception as exc:
            logger.warning(
                "%s[%s]: parallel page fetch failed (%s), refetching one page "
                "at a time",
                source,
                identifier,
                exc,
            )
        else:
            if items is not None:
                return items
            logger.warning(
                "%s[%s]: parallel page fetch came back with a different "
                "number of items than the first page's total, refetching one "
                "page at a time",
                source,
                identifier,
            )
    return _fetch_twice(
        lambda: _paginate_sequentially(fetch_page, pages),
        source,
        identifier,
        "sequential page fetch",
    )


def _paginate_sequentially(
    fetch_page: Callable[[int], dict], pages: _PageFormat
) -> list[dict]:
    """Fetch pages sequentially until the endpoint is exhausted."""
    items: list[dict] = []
    cursor = 0
    total: int | None = None
    while total is None or cursor < total:
        payload = fetch_page(cursor)
        page_total = pages.total(payload)
        if total is None:
            total = page_total
        elif page_total not in (0, total):
            raise IncompleteBoardResponse("pagination total changed during fetch")
        page = pages.items(payload, cursor)
        if not page:
            if cursor < total:
                raise IncompleteBoardResponse(
                    f"empty page at offset {cursor} before total {total}"
                )
            break
        items.extend(page)
        cursor += len(page)
        if cursor > total:
            raise IncompleteBoardResponse(
                f"received {cursor} items, more than reported total {total}"
            )
    pages.validate_ids(items)
    return items


def _paginate_in_parallel(
    fetch_page: Callable[[int], dict], pages: _PageFormat, workers: int
) -> list[dict] | None:
    """Fetch remaining pages concurrently when the total is known."""
    first = fetch_page(0)
    total = pages.total(first)
    items = list(pages.items(first, 0))
    if len(items) == total:
        return items
    if not items or len(items) > total:
        return None
    stride = len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        payloads = list(pool.map(fetch_page, range(stride, total, stride)))
    for payload in payloads:
        if pages.total(payload) not in (0, total):
            return None
        items.extend(pages.items(payload, len(items)))
    pages.validate_ids(items)
    return items if len(items) == total else None


def paginate_with_overlap(
    source: str,
    identifier: str,
    url: str,
    *,
    cursor_param: str,
    page_size_param: str,
    page_size: int,
    overlap: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    check_first_page: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Fetch every page of a listing that may change while it is paged.

    Pages are fetched one at a time, each starting `overlap` items before
    the previous one ended. A posting added or removed ahead of the cursor
    shifts every later page, so each page continues after the previous
    page's last item, found again in the overlap, instead of at a fixed
    offset: no posting listed throughout the fetch is skipped or repeated.
    If that item isn't on the page (a shift toward the start of `overlap`
    items or more), or an item repeats, the board is fetched once more and
    then fails.

    `check_first_page` sees the first response before any other page is
    requested, to reject a listing that can't be fetched completely.
    """
    if not 0 < overlap < page_size:
        raise ValueError("overlap must be positive and smaller than a page")
    pages = _PageFormat(total_key, items_key, id_key)

    def fetch_page(cursor: int) -> dict:
        response = get_with_retries(
            source, identifier, url,
            params={cursor_param: cursor, page_size_param: page_size},
        )
        return response.json()

    return _fetch_twice(
        lambda: _paginate_overlapping(
            fetch_page, pages, page_size, overlap, check_first_page
        ),
        source,
        identifier,
        "page fetch",
    )


def _paginate_overlapping(
    fetch_page: Callable[[int], dict], pages: _PageFormat, page_size: int,
    overlap: int, check_first_page: Callable[[dict], None] | None,
) -> list[dict]:
    items: list[dict] = []
    cursor = 0
    while True:
        payload = fetch_page(cursor)
        total = pages.total(payload)
        if cursor == 0 and check_first_page is not None:
            check_first_page(payload)
        page = pages.items(payload, cursor)
        end = cursor + len(page)
        if end > total:
            raise IncompleteBoardResponse(
                f"page at offset {cursor} ends at {end}, past its total {total}"
            )
        if items:
            anchor = normalize_job_id(items[-1][pages.id_key])
            page_ids = [normalize_job_id(item[pages.id_key]) for item in page]
            if anchor not in page_ids:
                raise IncompleteBoardResponse(
                    f"listing shifted by {overlap} or more items before "
                    f"offset {cursor}"
                )
            items.extend(page[page_ids.index(anchor) + 1:])
        else:
            items.extend(page)
        if end == total:
            break
        if len(page) < page_size:
            raise IncompleteBoardResponse(
                f"short page at offset {cursor}: {len(page)} item(s), "
                f"total {total}"
            )
        cursor = end - overlap
    pages.validate_ids(items)
    return items


def paginate_numbered_pages(
    source: str,
    identifier: str,
    fetch_page: Callable[[int], dict],
    *,
    page_size: int,
    total_key: str,
    items_key: str,
    id_key: str = "id",
    workers: int = 1,
    repair_rounds: int = 3,
    check_first_page: Callable[[dict], None] | None = None,
) -> list[dict]:
    """Fetch every page of a newest-first listing paged by number from 1.

    This is for an endpoint with a fixed page size and no offset, so pages
    can't overlap. Such a listing can order tied items differently on each
    request. A tied item on one side of a page boundary can then appear on
    both pages while its neighbour appears on neither, and the page lengths
    still add up to the total. Pages holding a repeated ID are fetched
    again and their items merged, up to `repair_rounds` times, until the
    distinct IDs reach the total.

    Every page must report the first page's total. Once every item is seen,
    the first page is fetched once more: an item on it that wasn't seen was
    added during the fetch. Either change, or repairs that don't reach the
    total, fetch the board once more and then fail it.

    `check_first_page` sees the first response before any other page is
    requested.
    """
    pages = _PageFormat(total_key, items_key, id_key)
    return _fetch_twice(
        lambda: _paginate_numbered(
            fetch_page, pages, page_size, workers, repair_rounds, check_first_page,
            f"{source}[{identifier}]",
        ),
        source,
        identifier,
        "page fetch",
    )


def _paginate_numbered(
    fetch_page: Callable[[int], dict], pages: _PageFormat, page_size: int,
    workers: int, repair_rounds: int,
    check_first_page: Callable[[dict], None] | None, label: str,
) -> list[dict]:
    first = fetch_page(1)
    total = pages.total(first)
    if check_first_page is not None:
        check_first_page(first)
    page_count = max(1, math.ceil(total / page_size))

    def read(number: int, payload: dict) -> list[dict]:
        if pages.total(payload) != total:
            raise IncompleteBoardResponse("pagination total changed during fetch")
        items = pages.items(payload, number)
        expected = min(page_size, total - (number - 1) * page_size)
        if len(items) != expected:
            raise IncompleteBoardResponse(
                f"page {number} has {len(items)} item(s), expected {expected} "
                f"of total {total}"
            )
        return items

    def fetch(numbers: list[int]) -> dict[int, list[dict]]:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            payloads = list(pool.map(fetch_page, numbers))
        return {number: read(number, payload) for number, payload in zip(numbers, payloads)}

    def item_id(item: dict) -> str:
        return normalize_job_id(item[pages.id_key])

    page_items = {1: read(1, first), **fetch(list(range(2, page_count + 1)))}
    seen: dict[str, dict] = {}
    for _round in range(repair_rounds + 1):
        for items in page_items.values():
            for item in items:
                seen.setdefault(item_id(item), item)
        if len(seen) >= total or _round == repair_rounds:
            break
        repeated = _pages_with_repeated_ids(page_items, item_id)
        logger.info(
            "%s: %d of %d items seen, refetching page(s) %s that repeat an item",
            label, len(seen), total, ", ".join(map(str, repeated)),
        )
        page_items.update(fetch(repeated))
    if len(seen) != total:
        raise IncompleteBoardResponse(
            f"{len(seen)} distinct item(s) after {repair_rounds} repair "
            f"round(s), total {total}"
        )
    if page_count > 1 and any(
        item_id(item) not in seen for item in read(1, fetch_page(1))
    ):
        raise IncompleteBoardResponse("an item was added during the fetch")
    return list(seen.values())


def _pages_with_repeated_ids(
    page_items: dict[int, list[dict]], item_id: Callable[[dict], str]
) -> list[int]:
    """Return the numbers of the pages holding an item another page also holds."""
    pages_by_id: dict[str, set[int]] = {}
    for number, items in page_items.items():
        for item in items:
            pages_by_id.setdefault(item_id(item), set()).add(number)
    return sorted({
        number
        for numbers in pages_by_id.values() if len(numbers) > 1
        for number in numbers
    })
