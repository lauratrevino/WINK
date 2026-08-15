"""A curated pool of motivational quotes shown on the dashboard.

Not major-specific — just a rotating statement meant to give students a
small lift. Quotes are widely-attributed and drawn from public knowledge;
treat the `source` field as "commonly attributed to" rather than a
notarized primary-source citation, and spot-check any that matter before
using them somewhere with higher stakes than a dashboard card.

get_motivation() picks one at random on every call. Pass the previously
shown quote's `text` as `exclude_text` to avoid an immediate repeat (the
caller is responsible for remembering what was last shown, e.g. in the
session — see dashboard.py).
"""

import random

MOTIVATIONAL_QUOTES = [
    {"text": "It always seems impossible until it's done.", "source": "Commonly attributed to Nelson Mandela"},
    {"text": "The expert in anything was once a beginner.", "source": "Commonly attributed to Helen Hayes"},
    {"text": "Success is the sum of small efforts, repeated day in and day out.", "source": "Commonly attributed to Robert Collier"},
    {"text": "You don't have to be great to start, but you have to start to be great.", "source": "Commonly attributed to Zig Ziglar"},
    {"text": "The beautiful thing about learning is that no one can take it away from you.", "source": "Commonly attributed to B.B. King"},
    {"text": "Whether you think you can or you think you can't, you're right.", "source": "Commonly attributed to Henry Ford"},
    {"text": "Education is the most powerful weapon which you can use to change the world.", "source": "Nelson Mandela"},
    {"text": "The only way to do great work is to love what you do.", "source": "Commonly attributed to Steve Jobs"},
    {"text": "Don't watch the clock; do what it does. Keep going.", "source": "Commonly attributed to Sam Levenson"},
    {"text": "It's not that I'm so smart, it's just that I stay with problems longer.", "source": "Commonly attributed to Albert Einstein"},
    {"text": "The future belongs to those who believe in the beauty of their dreams.", "source": "Commonly attributed to Eleanor Roosevelt"},
    {"text": "You are never too old to set another goal or to dream a new dream.", "source": "Commonly attributed to C.S. Lewis"},
    {"text": "Believe you can and you're halfway there.", "source": "Commonly attributed to Theodore Roosevelt"},
    {"text": "Small steps in the right direction can turn out to be the biggest step of your life.", "source": "Commonly attributed to Naeem Callaway"},
    {"text": "Push yourself, because no one else is going to do it for you.", "source": "Commonly attributed motivational saying"},
    {"text": "Great things are done by a series of small things brought together.", "source": "Commonly attributed to Vincent van Gogh"},
    {"text": "The secret of getting ahead is getting started.", "source": "Commonly attributed to Mark Twain"},
    {"text": "Difficult roads often lead to beautiful destinations.", "source": "Commonly attributed motivational saying"},
    {"text": "Your limitation—it's only your imagination.", "source": "Commonly attributed motivational saying"},
    {"text": "Great things never come from comfort zones.", "source": "Commonly attributed motivational saying"},
    {"text": "Dream it. Wish it. Do it.", "source": "Commonly attributed motivational saying"},
    {"text": "Success doesn't just find you. You have to go out and get it.", "source": "Commonly attributed motivational saying"},
    {"text": "The harder you work for something, the greater you'll feel when you achieve it.", "source": "Commonly attributed motivational saying"},
    {"text": "Don't stop when you're tired. Stop when you're done.", "source": "Commonly attributed motivational saying"},
    {"text": "Wake up with determination. Go to bed with satisfaction.", "source": "Commonly attributed motivational saying"},
    {"text": "Do something today that your future self will thank you for.", "source": "Commonly attributed motivational saying"},
    {"text": "Little things make big days.", "source": "Commonly attributed motivational saying"},
    {"text": "It's going to be hard, but hard does not mean impossible.", "source": "Commonly attributed motivational saying"},
    {"text": "Don't wait for opportunity. Create it.", "source": "Commonly attributed motivational saying"},
    {"text": "The key to success is to focus on goals, not obstacles.", "source": "Commonly attributed motivational saying"},
]


def get_motivation(exclude_text=None):
    """Returns a single randomly-selected quote dict with `text` and
    `source` keys. If exclude_text matches a quote's text and the pool has
    more than one entry, that quote is excluded from selection — pass the
    previously shown quote's text to avoid an immediate repeat."""
    pool = MOTIVATIONAL_QUOTES
    if exclude_text and len(pool) > 1:
        filtered = [q for q in pool if q["text"] != exclude_text]
        if filtered:
            pool = filtered
    return random.choice(pool)
