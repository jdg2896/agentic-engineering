## Worth following for ongoing signal

{% for feed in worth_following %}
- [{{ feed.name }}]({{ feed.url }}) — {{ feed.blurb }}
{% endfor %}
