import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator
from datetime import datetime
import pytz
import time

def get_content(item_url):
    """
    二级抓取：访问文章详情页，获取正文 HTML 并修复图片路径。
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    # 网站根路径，用于补全图片和链接的相对路径
    site_root = "https://paper.people.com.cn/rmrb/pc/"
    
    try:
        resp = requests.get(item_url, headers=headers, timeout=5)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 人民日报正文通常存放在 id 为 ozoom 的 div 容器中
        content_div = soup.find('div', id='ozoom')
        if content_div:
            # 将 Tag 对象转为字符串 HTML
            content_html = str(content_div)
            # 关键修复：将正文内所有的相对路径 ../../../ 替换为完整的网站根路径
            # 这样 RSS 阅读器才能正常显示图片
            fixed_html = content_html.replace('../../../', site_root)
            return fixed_html
        return "无法提取正文内容"
    except Exception as e:
        return f"详情页请求失败: {e}"

def get_all_data():
    """
    核心逻辑：先解析版面导航(Swiper)，再遍历每个版面下的新闻。
    """
    # 1. 处理日期和基础 URL
    tz = pytz.timezone('Asia/Shanghai')
    today = datetime.now(tz)
    date_path = today.strftime("%Y%m/%d") # 格式化为 202604/26
    
    site_root = "https://paper.people.com.cn/rmrb/pc/"
    # 列表页的目录路径
    base_layout_url = f"{site_root}layout/{date_path}/"
    # 今日头版的 URL，作为爬取的入口
    first_page_url = f"{base_layout_url}node_01.html"
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    all_articles = []

    # 2. 获取首页并解析 swiper-container 里的版面目录
    print(f"正在访问首页获取目录: {first_page_url}")
    try:
        resp = requests.get(first_page_url, headers=headers)
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            print(f"无法访问，请确认今日报纸是否已发布（状态码: {resp.status_code}）")
            return []
    except Exception as e:
        print(f"网络连接错误: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')
    # 根据用户发现，版面信息存在 swiper-container 中
    swiper = soup.find('div', class_='swiper-container')
    if not swiper:
        print("未能在页面中找到 swiper-container 版面导航。")
        return []

    # 提取所有版面的链接和名称
    sections = []
    for link_tag in swiper.find_all('a', id='pageLink'):
        sec_name = link_tag.get_text(strip=True)
        sec_href = link_tag.get('href') # 得到类似 "node_02.html"
        
        sections.append({
            'name': sec_name,
            # 将相对链接拼成完整 URL
            'url': base_layout_url + sec_href 
        })

    print(f"检测完毕，今日共有 {len(sections)} 个版面。开始深度抓取...")

    # 3. 循环遍历每个版面链接
    for sec in sections:
        print(f"--- 正在处理: {sec['name']} ---")
        try:
            sec_resp = requests.get(sec['url'], headers=headers, timeout=5)
            sec_resp.encoding = 'utf-8'
            sec_soup = BeautifulSoup(sec_resp.text, 'html.parser')
            
            # 找到当前版面下的新闻列表
            news_list = sec_soup.find('ul', class_='news-list')
            if news_list:
                for li in news_list.find_all('li'):
                    a_tag = li.find('a')
                    if a_tag:
                        title = a_tag.get_text(strip=True)
                        # 清洗新闻详情页的相对链接 ../../../content/... 为绝对链接
                        raw_href = a_tag.get('href').replace('../../../', '')
                        full_item_link = site_root + raw_href
                        
                        print(f"  正在抓取全文: {title[:15]}...")
                        # 调用二级抓取函数获取正文
                        detail_content = get_content(full_item_link)
                        
                        # 存入列表，标题带上版面信息
                        all_articles.append({
                            'title': f"[{sec['name']}] {title}",
                            'link': full_item_link,
                            'content': detail_content
                        })
                        # 礼貌抓取，避免请求过快被封 IP
                        time.sleep(0.2)
        except Exception as e:
            print(f"抓取版面 {sec['name']} 时出错: {e}")
            continue
            
    return all_articles

def generate_rss(articles):
    """
    根据抓取到的数据生成 RSS 2.0 规范的 XML 文件。
    """
    fg = FeedGenerator()
    fg.title('人民日报 - 每日全文订阅')
    fg.link(href='https://paper.people.com.cn/', rel='alternate')
    fg.description('基于 Python 自动化抓取的人民日报全版面、全正文 RSS 服务')
    fg.language('zh-CN')

    # 使用 reversed 让第一版新闻在 RSS 阅读器中排在最上方
    for art in reversed(articles): 
        fe = fg.add_entry()
        fe.title(art['title'])
        fe.link(href=art['link'])
        fe.id(art['link'])
        # content 标签用于存储抓取的 HTML 正文
        fe.content(art['content'], type='html') 
        # 发布日期设定为当前时间
        fe.pubDate(datetime.now(pytz.timezone('Asia/Shanghai')))

    # 生成文件
    file_name = 'rmrb_fulltext.xml'
    fg.rss_file(file_name, pretty=True)
    print(f"\n✅ 任务完成！共处理 {len(articles)} 篇文章。")
    print(f"📁 RSS 文件已保存至: {file_name}")

if __name__ == "__main__":
    # 执行主程序
    data = get_all_data()
    if data:
        generate_rss(data)
    else:
        print("今日数据抓取为空，请检查网络或网站是否更新。")
