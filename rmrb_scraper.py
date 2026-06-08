import aiohttp
import asyncio
import os
import pytz
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from feedgen.feed import FeedGenerator

# --- 自动获取日期配置 ---
def get_bj_date():
    tz = pytz.timezone('Asia/Shanghai')
    return datetime.now(tz).strftime("%Y%m/%d")

DATE_PATH = get_bj_date()  # 格式化为: 202606/08
SITE_ROOT = "https://paper.people.com.cn/rmrb/pc/"
BASE_LAYOUT_URL = f"{SITE_ROOT}layout/{DATE_PATH}/"
FIRST_PAGE_URL = f"{BASE_LAYOUT_URL}node_01.html"

DEFAULT_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

async def fetch(url, session):
    try:
        async with session.get(url, headers=DEFAULT_HEADERS, timeout=15) as response:
            if response.status == 200:
                raw_data = await response.read()
                return raw_data.decode('utf-8', errors='ignore')
            else:
                print(f"❌ 网页请求失败 [状态码 {response.status}]: {url}")
                return ""
    except Exception as e:
        print(f"❌ 网络请求异常 [{type(e).__name__}]: {url}")
        return ""

async def get_content(sec_name, title, item_url, session):
    """
    二级抓取：异步获取文章详情页，提取正文并修复图片/音频等相对路径
    """
    html = await fetch(item_url, session)
    if not html:
        print(f"⚠️ 跳过解析（详情页HTML为空）: {item_url}")
        return None
        
    soup = BeautifulSoup(html, 'html.parser')
    content_div = soup.find('div', class_='article')
    
    if content_div:
        # 清理冗余的样式或脚本
        for tag in content_div.find_all(['style', 'script']):
            tag.decompose()
            
        # 修正图片/多媒体路径：人民日报的相对路径是 ../../../ 改用 urljoin 自动智能补全
        for img in content_div.find_all('img'):
            if img.get('src'):
                img['src'] = urljoin(item_url, img['src'])
                
        for a in content_div.find_all('a'):
            if a.get('href'):
                a['href'] = urljoin(item_url, a['href'])

        final_title = f"[{sec_name}] {title}"
        print(f"✅ 成功抓取: {final_title}")
        
        return {
            'title': final_title,
            'link': item_url,
            'content': str(content_div)
        }
        
    print(f"❌ 无法解析正文（未找到 class='article' 标签）: {item_url} | 标题: {title}")
    return None

async def parse_single_section(sec_name, sec_url, session):
    """
    解析单个版面，提取该版面下的所有文章链接
    """
    html = await fetch(sec_url, session)
    if not html:
        print(f"⚠️ 无法获取版面页面: {sec_name} -> {sec_url}")
        return []
        
    soup = BeautifulSoup(html, 'html.parser')
    news_list = soup.find('ul', class_='news-list')
    
    articles_in_sec = []
    if news_list:
        for li in news_list.find_all('li'):
            a_tag = li.find('a')
            if a_tag and a_tag.get('href'):
                title = a_tag.get_text(strip=True)
                # 使用 urljoin 完美替代复杂的 replace('../../../', '')
                full_item_link = urljoin(sec_url, a_tag.get('href'))
                articles_in_sec.append((sec_name, title, full_item_link))
    return articles_in_sec

async def main():
    async with aiohttp.ClientSession() as session:
        print(f"🚀 自动化抓取启动 | 目标日期: {DATE_PATH}")
        print(f"🔗 正在请求首页获取目录: {FIRST_PAGE_URL}")
        
        index_html = await fetch(FIRST_PAGE_URL, session)
        if not index_html:
            print(f"🛑 错误: 无法访问首页，请确认今日报纸是否已发布或检查网络。")
            return

        soup = BeautifulSoup(index_html, 'html.parser')
        swiper = soup.find('div', class_='swiper-container')
        if not swiper:
            print("🛑 错误: 首页解析失败，未能在页面中找到 'swiper-container' 版面导航！")
            return

        # 1. 提取所有版面链接
        sections_tasks = []
        for link_tag in swiper.find_all('a', id='pageLink'):
            sec_name = link_tag.get_text(strip=True)
            sec_href = link_tag.get('href')
            if sec_href:
                sec_url = urljoin(BASE_LAYOUT_URL, sec_href)
                # 扔进并发版面解析队列
                sections_tasks.append(parse_single_section(sec_name, sec_url, session))

        print(f"📊 首页解析成功，发现 {len(sections_tasks)} 个版面。开始同步获取各版面新闻列表...")
        
        # 2. 并发获取所有版面下的文章列表
        sections_results = await asyncio.gather(*sections_tasks)
        
        # 汇总所有文章的抓取任务
        article_tasks = []
        for article_list in sections_results:
            for sec_name, title, full_item_link in article_list:
                article_tasks.append(get_content(sec_name, title, full_item_link, session))

        total_links = len(article_tasks)
        print(f"📦 统计完毕！今日共有文章 {total_links} 篇。开始全异步并发下载全文...")

        # 3. 顺序修正（让版面 01、02 按原本顺序正序排列，因为阅读器通常把最新入库的堆在最上面）
        # 如果你喜欢头版在最上面，保持这里不 reverse，但在写入 RSS 时注意顺序即可。
        # 这里我们延续你原代码的 reversed 逻辑，不在数组层倒序，而在生成 entry 时处理。
        
        # 4. 并发抓取所有详情页
        results = await asyncio.gather(*article_tasks)
        articles = [r for r in results if r]
        
        print(f"📊 抓取阶段结束。共发现链接 {total_links} 条，成功解析正文 {len(articles)} 篇。")

        # 5. 生成 RSS 文件
        fg = FeedGenerator()
        fg.title('人民日报 - 每日全文订阅')
        fg.link(href='https://paper.people.com.cn/', rel='alternate')
        fg.description('基于 Python 自动化高并发抓取的人民日报全版面全正文 RSS')
        fg.language('zh-CN')

        rss_count = 0
        # 采用 reversed 保持你原有的阅读顺序习惯
        for art in reversed(articles):
            fe = fg.add_entry()
            fe.title(art['title'])
            fe.link(href=art['link'])
            
            # 【核心修复】防止 URL 重复覆盖，合成唯一 ID
            unique_id = f"{art['link']}#{art['title']}"
            fe.id(unique_id)
            
            fe.content(art['content'], type='html')
            fe.pubDate(datetime.now(pytz.timezone('Asia/Shanghai')))
            rss_count += 1

        print(f"📝 正在写入 RSS 文件，共 {rss_count} 条 Item...")
        fg.rss_file('rmrb_fulltext.xml', pretty=True)
        print(f"✨ 成功！文件已保存至: rmrb_fulltext.xml")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
