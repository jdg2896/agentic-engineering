## Worth following for ongoing signal

{% for feed in worth_following %}
- [{{ feed.name }}]({{ feed.url | link_url }}) — {{ feed.line }}
{% endfor %}
