#!/usr/bin/env python3
import requests
import json
from datetime import datetime


def get_visits(content_id, cloud_session_token, cloud_id="c0afe2f3-c4a1-491e-8489-4580951afaf6", years_search_length=1):
    today_date = datetime.today()
    furthest_date = today_date.replace(year=today_date.year-years_search_length)
    today_date_string = today_date.strftime("%Y-%m-%d")
    furthest_date_string = furthest_date.strftime("%Y-%m-%d")

    url = f"https://openedx.atlassian.net/gateway/api/ex/confluence/{cloud_id}/analytics/content/viewsByDate?contentId={content_id}&contentType=page&fromDate={furthest_date_string}T04%3A00%3A00.000Z&toDate={today_date_string}T03%3A59%3A59.999Z&type=total&period=week&timezone=America%2FNew_York"
    cookies = {
        "cloud.session.token": cloud_session_token,
    }

    response = requests.get(
        url,
        cookies=cookies
    )
    if response.status_code == 200:
        """
        visitined_info = { 'viewsByDate': [{'date': '', 'total': ##}, {...}, {...}, ...]}
        """
        visited_dates_info = json.loads(response.text)['viewsByDate']
        for visit_date_info in visited_dates_info:
            # date = "2021-06-28T04:00:00.000Z"
            visit_date_string = visit_date_info['date'][:visit_date_info['date'].index("T")]
            visit_date_datetime = datetime.strptime(visit_date_string,"%Y-%m-%d")
            visit_date_info['date'] = visit_date_datetime
        return sorted(visited_dates_info, key=lambda x: x['date'])


