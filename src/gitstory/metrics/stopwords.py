"""English and Portuguese stopwords for the commit-message word counts.

Function words only: verbs that characterise a commit style ("add", "fix", "update") are
deliberately kept, because they are exactly what the word list is meant to show.
"""

from __future__ import annotations

_ENGLISH = """
a about above after again against all also am an and any are aren't as at be because been
before being below between both but by can can't cannot could couldn't did didn't do does
doesn't doing don't down during each etc few for from further had hadn't has hasn't have
haven't having he her here hers herself him himself his how i i'd i'll i'm i've if in into
is isn't it it's its itself just let's me more most mustn't my myself no nor not now of off
on once only or other ought our ours ourselves out over own per same shan't she should
shouldn't so some such than that that's the their theirs them themselves then there there's
these they they'd they'll they're they've this those through to too under until up us very
via was wasn't we we'd we'll we're we've were weren't what what's when where which while who
whom why will with won't would wouldn't you you'd you'll you're you've your yours yourself
yourselves
"""

_PORTUGUESE = """
a ainda ali ao aos apenas após aquela aquelas aquele aqueles aquilo as até aqui bem cada
com como contra da das de dela delas dele deles depois desde dessa dessas desse desses
desta destas deste destes disso disto do dos e ela elas ele eles em entre era eram essa
essas esse esses esta estas este estes está estão eu foi foram há isso isto já lá lhe lhes
mais mas me mesma mesmo meu meus minha minhas muito na nas nem no nos nós nossa nossas
nosso nossos num numa o os ou outra outras outro outros para pela pelas pelo pelos por
porque pois pra quais qual quando quanto que quem sem ser será seu seus só sob sobre sua
suas também te tem têm tinha toda todas todo todos tu tua tuas teu teus um uma umas uns vez
vezes vos à às é são então
"""

STOPWORDS: frozenset[str] = frozenset((_ENGLISH + _PORTUGUESE).split())
