import calendar
from django import template
from datetime import date, timedelta

register = template.Library()

@register.inclusion_tag('scheduler/_calendar.html')
def calendar_grid(year, month, selected_coach_id=None, user=None):
    # ... this function remains unchanged ...
    cal = calendar.monthcalendar(year, month)
    today = date.today()
    view_date = date(year, month, 1)

    first_day_of_month = view_date
    last_day_of_previous_month = first_day_of_month - timedelta(days=1)
    prev_month_date = last_day_of_previous_month.replace(day=1)

    _, num_days_in_month = calendar.monthrange(year, month)
    last_day_of_current_month = view_date.replace(day=num_days_in_month)
    first_day_of_next_month = last_day_of_current_month + timedelta(days=1)
    next_month_date = first_day_of_next_month
    
    return {
        'calendar': cal, 
        'today': today, 
        'view_date': view_date,
        'prev_month_date': prev_month_date,
        'next_month_date': next_month_date,
        'selected_coach_id': selected_coach_id,
        'user': user,
    }

# We are keeping get_item for now, but adding a better tag for this specific case
@register.filter
def get_item(dictionary, key):
    """Allows dictionary lookups using a variable key in templates."""
    return dictionary.get(key)

@register.simple_tag
def is_checked(availability_map, slot_pk, day_index):
    """
    Checks if a specific slot/day combination exists in the availability map.
    Returns the string 'checked' if it exists, otherwise an empty string.
    """
    if (slot_pk, day_index) in availability_map:
        return "checked"
    return ""

@register.filter
def lookup(dictionary, key):
    """
    Allows dictionary lookups using a variable key in templates.
    Usage: {{ dict|lookup:key }}
    """
    if dictionary and key is not None:
        return dictionary.get(key, [])
    return []

@register.filter
def contains(list_or_dict, item):
    """
    Checks if an item is in a list or dictionary.
    Usage: {{ list|contains:item }}
    """
    if list_or_dict is None:
        return False
    try:
        return item in list_or_dict
    except (TypeError, ValueError):
        return False
