import os
import re
import argparse
import requests
import time
import random
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.progress import Progress
from rich import print

def get_total_pages():
    """Fetch the first page and determine how many pagination pages exist."""
    soup = fetch_dom(BASE_URL)
    return max_page_no(soup)

BASE_URL = "https://meirentu.cc/"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/114.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# persistent session with retry support
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(max_retries=3)
session.mount("http://", adapter)
session.mount("https://", adapter)
session.headers.update(HEADERS)


def fetch_dom(url):
    with session.get(url, timeout=10) as resp:
        resp.raise_for_status()
        html = resp.text
    return BeautifulSoup(html, "html.parser")


def get_album_list(page_no):
    # meirentu pagination uses index/<num>.html for pages beyond the first
    if page_no == 1:
        url = BASE_URL
    else:
        url = urljoin(BASE_URL, f"index/{page_no}.html")
    soup = fetch_dom(url)
    cards = soup.select("li.i_list > a")
    entries = []

    for card in cards:
        href = card.get("href")
        title_tag = card.select_one(".postlist-imagenum span")
        if href and title_tag:
            entries.append({
                "title": title_tag.text.strip(),
                "url": urljoin(BASE_URL, href)
            })
    print(f"[green]🧭 第 {page_no} 页发现 {len(entries)} 个相册[/green]")
    return entries


def max_page_no(soup):
    page_links = soup.select(".page a")
    nums = [int(m.group()) for a in page_links if (m := re.search(r"\d+", a.text.strip()))]
    return max(nums) if nums else 1


def fetch_images_from_page(url):
    try:
        soup = fetch_dom(url)
        imgs = soup.select(".content_left img")
        return [img.get("src") for img in imgs if img.get("src")]
    except Exception as e:
        print(f"[red]抓取失败 {url}: {e}[/red]")
        return []


def get_all_photos(item, max_workers=1):
    base = item["url"].rstrip("/")
    first = fetch_dom(base)
    # 获取所有分页链接
    page_links = first.select(".page a")
    page_urls = set()
    for a in page_links:
        href = a.get("href")
        if href:
            page_urls.add(urljoin(BASE_URL, href))
    # 加上第一页
    page_urls.add(base)
    page_urls = list(page_urls)

    photos = []
    with Progress() as progress:
        task = progress.add_task(f"[cyan]抓取图片链接 {item['title']}...", total=len(page_urls))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(fetch_images_from_page, url): url for url in page_urls}
            for future in as_completed(futures):
                photos.extend(future.result())
                progress.advance(task)
    return photos


def download_image(url, folder, idx, referer=None, retries=3):
    ext = os.path.splitext(url)[-1].split("?")[0]
    if not ext or len(ext) > 5:
        ext = ".jpg"
    filename = os.path.join(folder, f"{idx+1:03}{ext}")
    headers = HEADERS.copy()
    if referer:
        headers["Referer"] = referer
    for attempt in range(retries):
        try:
            with session.get(url, headers=headers, stream=True, timeout=10) as r:
                r.raise_for_status()
                with open(filename, "wb") as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
            return True, url
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(1)  # 等待1秒后重试
            else:
                return False, f"{url} | {e}"


def save_photos(title, photos, referer=None, max_workers=3):
    safe_title = re.sub(r'[\\/:"*?<>|]+', "_", title)
    folder = os.path.join("meirentu_downloads", safe_title)
    os.makedirs(folder, exist_ok=True)

    print(f"\n[bold yellow]📥 开始下载 [{title}] 共 {len(photos)} 张图片[/bold yellow]")

    failed_list = []
    with Progress() as progress:
        task = progress.add_task(f"[magenta]下载中...", total=len(photos))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(download_image, url, folder, idx, referer) for idx, url in enumerate(photos)]
            for future in as_completed(futures):
                success, msg = future.result()
                if not success:
                    print(f"[red]下载失败: {msg}[/red]")
                    failed_list.append(msg)
                progress.advance(task)
    if failed_list:
        with open(os.path.join(folder, "failed.txt"), "w", encoding="utf-8") as f:
            for line in failed_list:
                f.write(line + "\n")


def main(start, end):
    if end <= 0:
        try:
            print("[cyan]🔍 正在获取页面总数...[/cyan]")
            end = get_total_pages()
            print(f"[green]共 {end} 页[/green]")
        except Exception as e:
            print(f"[red]获取总页数失败: {e}[/red]")
            return
    for page in range(start, end + 1):
        print(f"\n[bold blue]🔎 抓取第 {page} 页相册列表...[/bold blue]")
        try:
            albums = get_album_list(page)
        except Exception as e:
            print(f"[red]抓取失败: {e}[/red]")
            continue

        time.sleep(random.uniform(0.5, 1.5))

        for album in albums:
            print(f"\n[white on blue]>>> 处理相册: {album['title']}[/white on blue]")
            try:
                photo_links = get_all_photos(album)
                save_photos(album["title"], photo_links, referer=album["url"])
                time.sleep(random.uniform(0.5, 1.5))
            except Exception as e:
                print(f"[red]相册失败: {album['title']} | {e}[/red]")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="抓取 meirentu.cc 图片")
    parser.add_argument("--start", type=int, default=1, help="起始页码")
    parser.add_argument("--end", type=int, default=0, help="结束页码(0表示抓取到最后一页)")
    args = parser.parse_args()
    try:
        main(args.start, args.end)
    except KeyboardInterrupt:
        print("\n[bold red]⚠️ 用户中断程序[/bold red]")
