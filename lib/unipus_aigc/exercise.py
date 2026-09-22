# -*- coding: utf-8 -*-
"""任务生成（教学任务编排）。

把平台上**已经跑通的九个应用**编排成"像真题一样"的**有序任务**：一条任务 =
若干步，每步写明用哪个应用、参数是什么、上一步的什么产出喂给下一步。

.. note::
   **这一层不新增任何平台调用。** 每个应用自己内部的异步链（``submit`` → 轮询 →
   结果）早就封在各自的 API 里了（``speech.say`` / ``review.essay`` /
   ``image_gen.draw_and_wait`` / ``question_gen.generate``），所以"多步任务"只能靠
   **跨应用**组合来做——再包一层轮询没有意义。

.. warning::
   **生成（``gen``）永远不碰平台。** 它只读本地素材库、只写本地文件。
   真跑是另一个子命令（``exercise run``），一次一条，必须显式给 ``--go``。

四条链路各有各的坑，都写在任务卡的 ``notes`` 里，也写在
``docs/call-chains.md`` §12：

* **TTS → 口语评阅**：合成的是**标准发音**，评阅分数基本都偏高
  （实测 95.0 / 98.0）。这条任务的价值在"给学生一段范读"，
  不在"测出学生多差"。
* **作文题目 → 作文评阅**：第一步生成的是**题目**，不是范文。
  平台的写作能力产出的文章拿去做评阅只会拿高分，学生学不到东西。
  **而且它其实产不出作文**——实测 `lm/content/commonContinueWrite` 给一句
  题干做 `before`，回来的是**中文的写作建议**，作文评阅直接回 `score=0`
  + "没有足够的英文单词。"。所以 `--auto` 的替身用的是**素材库里预写的范文**。
* **阅读材料 → 出题 → 答题**：``rm/delete`` **不存在**，材料建了就删不掉，
  所以默认走 ``preview``（同步回题面、不落库、无 ``quesId``），
  建材料那一步标成 ``app?``（默认不执行）。**而且"答题"那一步本来就没有
  可用的接口**——文档的 ``ques/ans`` 实测 404，前端产物里也搜不到它，
  所以那一步是 ``human``。
* **翻译 → 翻译评阅**：op36 **只打分，不产出译文**。``translation`` 是提交时
   那份的原样存档，别读成"平台改过的"。

命名：本域叫 ``exercise``（"习题/任务"）而**不是** ``task``。``TaskStatus`` /
``client.submit_task`` 已经用 "task" 指**平台侧的任务**，两者混用会让
"这一步失败了"到底是任务卡的问题还是平台任务的问题分不清。
"""

import json
import os
import random
import re
import time

from . import config
from .constants import Level
from .errors import AigcError
from .question_gen import EDUCATION, PLOY_CODES
from .speech import SpeechAPI
from .trans_review import SUPPORTED_LANGS


class LocalCheckError(AigcError):
    """本地校验没过——**一个请求都没发**。CLI 把它映射成退出码 2（用法错误）。

    这一类跟"接口报错"必须分开：本地能判定的问题（链路名写错、素材不够、
    前向引用、参数不在白名单里）如果混进退出码 1，用户会以为平台坏了。
    """


# ----------------------------------------------------------------------
# 素材库
# ----------------------------------------------------------------------
#
# 本地静态素材，**不调平台**：可复现、零残留、快。
#
# 为什么不用平台的文本生成现造素材（`article/common-continue` 那条不依赖文章、
# 零残留）：它慢（每条要几秒）、**不可复现**（同一个 seed 两次跑内容不一样）、
# 而且要 token。那个能力留给 `run --auto` 去代做 `human` 步骤。

#: 英文朗读短文（链路 ``tts-oral``）。要**适合朗读**：长短句交错、
#: 含少量易错音（``th`` / ``v`` / ``r`` / 词尾 ``-s``），难度分高中/大学两档。
BANK_PASSAGE_EN = [
    {"title": "A Quiet Morning", "level": 1,
     "text": "The library opens at eight in the morning. Students arrive early to "
             "find a seat by the window. Some read quietly, while others work on "
             "their laptops. The smell of coffee fills the air, and the morning "
             "passes peacefully."},
    {"title": "The School Garden", "level": 1,
     "text": "Our school has a small garden behind the science building. Every "
             "spring, students plant vegetables and flowers there. We take turns "
             "watering them during the lunch break. By summer, the whole garden is "
             "full of colour."},
    {"title": "Learning Online", "level": 0,
     "text": "Online courses have changed the way we learn. We can now attend "
             "lectures from anywhere in the world. However, staying focused at home "
             "is not always easy. Many students find that a fixed schedule helps a "
             "great deal."},
    {"title": "A Letter to My Teacher", "level": 1,
     "text": "Dear Miss Chen, thank you for helping me with my English last term. "
             "I used to be afraid of speaking in class. You always encouraged me to "
             "try again, even when I made mistakes. I have made real progress "
             "because of your patience."},
    {"title": "The Longest Journey", "level": 0,
     "text": "Last summer, I travelled to the mountains with my family. The road "
             "was narrow and the weather changed very quickly. We walked for six "
             "hours before we reached the top. The view made every single step "
             "worthwhile."},
    {"title": "Recycling at Home", "level": 1,
     "text": "Recycling starts with small habits at home. We keep three boxes in "
             "the kitchen for paper, plastic and glass. My brother thought it was "
             "boring at first. Now he reminds the whole family to sort the rubbish "
             "every evening."},
    {"title": "The Science Fair", "level": 1,
     "text": "Our class built a model of the water cycle for the science fair. We "
             "used a plastic bottle, some cotton wool and a small lamp. When the "
             "lamp was switched on, drops of water appeared on the inside of the "
             "bottle. Everyone clapped."},
    {"title": "Working from Home", "level": 0,
     "text": "Working from home sounds comfortable, but it has its own "
             "difficulties. The line between work and rest becomes unclear. Some "
             "people finish their tasks very late at night. Others learn to close "
             "the laptop at six and go for a walk."},
    {"title": "A Trip to the Museum", "level": 1,
     "text": "The city museum is free on the first Sunday of every month. My "
             "father took me there to see an exhibition about ancient writing. I "
             "was surprised to learn that people once wrote on animal bones. We "
             "stayed until the guards asked us to leave."},
    {"title": "The New Student", "level": 1,
     "text": "A new student joined our class this week. Her name is Lily, and she "
             "moved here from another city. At first, she sat alone and said very "
             "little. Yesterday, she raised her hand and answered a difficult "
             "question. Everyone turned to look at her."},
    {"title": "Why We Sleep", "level": 0,
     "text": "Scientists still do not fully understand why we sleep. What they do "
             "know is that the brain uses this time to organise what we learned "
             "during the day. Students who sleep well remember more than those who "
             "study all night."},
    {"title": "The Village Market", "level": 1,
     "text": "Every Saturday, the village market opens before sunrise. Farmers "
             "bring vegetables that were picked the evening before. My grandmother "
             "always buys the same kind of bread from the same woman. They have "
             "known each other for forty years."},
    {"title": "A Difficult Choice", "level": 0,
     "text": "Choosing a university is harder than it looks. Some students follow "
             "the advice of their parents, and others follow their friends. The "
             "most useful thing I did was to visit three campuses and talk to "
             "students there. That changed my mind completely."},
    {"title": "Volunteering on Sundays", "level": 1,
     "text": "Every Sunday morning, a group of us clean the river bank near the "
             "old bridge. We wear thick gloves and carry large bags. Last month we "
             "collected more than thirty kilograms of plastic. The birds have come "
             "back to that part of the river."},
    {"title": "The Old Bookshop", "level": 0,
     "text": "There is an old bookshop at the end of our street. The owner knows "
             "every shelf by heart. If you describe a book you cannot remember, he "
             "will usually find it within a minute. He has been running the shop "
             "since he was twenty-one."},
    {"title": "Learning to Cook", "level": 1,
     "text": "My mother decided that I should learn to cook before I left home. "
             "We started with simple things like rice and soup. The first three "
             "attempts were terrible. By the end of the month, I could prepare a "
             "meal for four people."},
]

#: 英语作文题（链路 ``essay-review``）。每条都带**交际情境**和内容要点——
#: 真实作文题都长这样，光给一个话题不像题。
BANK_ESSAY_PROMPTS = [
    {"topic": "Online Learning", "genre": "议论文", "level": 1, "words": "120-150",
     "prompt": "Your school newspaper is collecting opinions on online learning. "
               "Write an article discussing whether online classes can replace "
               "traditional classrooms.",
     "hints": ["说明线上学习的两个优势", "指出至少一个明显不足", "给出你的结论"],
     "sample": 'Online classes have become common in recent years. They save time, and students can review a lecture as many times as they wish. However, I do not think they can fully replace a traditional classroom. In a real classroom, a teacher notices when a student looks confused and slows down. Classmates ask questions that nobody had thought of, and the discussion moves forward. At home, in front of a screen, it is easy to close the laptop and stop paying attention. Online learning is a useful tool, but it works best together with a real classroom.'},
    {"topic": "A Memorable Trip", "genre": "记叙文", "level": 2, "words": "80-100",
     "prompt": "Your English club is publishing a collection of short travel "
               "stories. Write about a trip that you still remember clearly.",
     "hints": ["交代时间地点和同行的人", "写一件具体发生的事", "说明为什么至今难忘"],
     "sample": 'Two summers ago my family travelled to a small town in the mountains. We left before sunrise and drove for five hours. On the way the car broke down, and we waited by the road for almost two hours. A farmer took us to his house and gave us tea while a mechanic repaired the car. We finally reached the town after dark. I still remember the taste of that tea and the sound of the rain on the roof. That trip was not comfortable, but it is the one I remember best.'},
    {"topic": "Protecting the Environment", "genre": "议论文", "level": 1,
     "words": "120-150",
     "prompt": "The city council is asking students for ideas on reducing plastic "
               "waste. Write a proposal explaining what your school could do.",
     "hints": ["描述目前的一个具体问题", "提出两条可执行的建议", "说明预期效果"],
     "sample": 'Our school produces a large amount of plastic waste every day. Most of it comes from the bottles sold in the school shop. I would like to suggest two changes. First, the shop could sell drinks in returnable bottles and charge a small deposit, which is returned when the bottle comes back. Second, every classroom could have a box for paper that has been used on one side only. These two measures are simple, but they could reduce our waste by half within a term.'},
    {"topic": "My Ideal Job", "genre": "说明文", "level": 2, "words": "80-100",
     "prompt": "Your class is preparing a career day. Write a short passage "
               "introducing the job you would most like to do in the future.",
     "hints": ["说明这是什么工作", "写出你选择它的两个理由", "谈谈需要做什么准备"],
     "sample": 'My ideal job is to be a primary school English teacher. I have two reasons for this choice. First, I enjoy explaining things to other people, and I am patient when someone does not understand at once. Second, I still remember my own first English teacher, who made a difficult subject feel easy and interesting. To prepare for this job I need to improve my spoken English and learn how children think and learn. It will take years of study, but I am willing to start now.'},
    {"topic": "Reading Habits", "genre": "图表作文", "level": 0, "words": "150-180",
     "prompt": "A recent survey shows that students spend far more time on short "
               "videos than on books. Write an essay analysing this situation.",
     "hints": ["概括调查反映的趋势", "分析两个可能的原因", "提出你的看法"],
     "sample": 'A recent survey of our school shows a clear change in reading habits. Students spend about two hours a day on short videos but only fifteen minutes on books. There are two likely reasons. Short videos are designed to be finished in seconds, so they give quick and easy pleasure, while a book asks for patience. In addition, a phone is always within reach. In my opinion, we should not blame students but design better conditions: a quiet reading period, and teachers who talk about the books they are reading.'},
    {"topic": "A Letter of Thanks", "genre": "应用文", "level": 1, "words": "80-100",
     "prompt": "Write a letter to a teacher who helped you overcome a difficulty "
               "in your studies.",
     "hints": ["说明写信的目的", "具体写他/她帮了你什么", "表达感谢并说明你的变化"],
     "sample": 'Dear Mr Li, I am writing to thank you for the help you gave me in mathematics last term. In the first month of the year I failed two tests and I was ready to give up. You asked me to stay after class three times a week and explained the same idea again and again without ever showing impatience. By the end of the term my score had risen from 52 to 81. More importantly, I no longer tell myself that I am simply bad at mathematics. Thank you for your patience. Yours sincerely, Chen Wei.'},
    {"topic": "Technology and Friendship", "genre": "议论文", "level": 0,
     "words": "150-180",
     "prompt": "Some people say social media brings friends closer; others say it "
               "makes real friendship weaker. Write an essay giving your view.",
     "hints": ["呈现两种对立观点", "用一个例子支持你的立场", "承认对方观点合理之处"],
     "sample": 'Some people say that social media brings friends closer, while others believe it weakens real friendship. In my view, both are partly right. Social media is excellent at keeping a light contact alive: I can send a message to a friend in another city and receive an answer within a minute. But a friendship built only on messages stays at the surface. Last winter my closest friend moved away, and our chat history is long, yet the conversation that mattered was the one we had face to face before he left. Technology helps friendship survive distance, but it cannot replace presence.'},
    {"topic": "Volunteering", "genre": "记叙文", "level": 1, "words": "120-150",
     "prompt": "Your school is recruiting volunteers for a community service "
               "programme. Write about a volunteering experience of your own.",
     "hints": ["说明你做了什么", "写一个遇到的困难", "写出你的收获"],
     "sample": "Last autumn I joined a group that visits an old people's home every Saturday. My job was simple: I read newspapers aloud and talked with the residents. In the beginning I was awkward, and one afternoon I could not understand what an old man was saying because of his accent. Instead of pretending, I asked him to repeat and we laughed about it. By the end of the term, he told me I had become his best Saturday. I learned that volunteering is not only about giving help; it is also about learning how to listen."},
    {"topic": "Healthy Eating", "genre": "说明文", "level": 2, "words": "80-100",
     "prompt": "Your school clinic is publishing a guide on healthy eating for "
               "teenagers. Write a short passage for it.",
     "hints": ["指出中学生饮食的一个常见问题", "给出两条具体建议", "提醒坚持的重要性"],
     "sample": 'Many students in our school skip breakfast and eat snacks instead. This is a common problem, and it has a simple cause: the mornings are rushed. I have two suggestions. First, prepare breakfast the night before, such as bread, milk and a piece of fruit, so that it takes two minutes to eat. Second, keep a bottle of water on the desk instead of a sweet drink, because hunger and thirst are often confused. None of this is difficult. What is difficult is doing it every day.'},
    {"topic": "The Value of Failure", "genre": "议论文", "level": 0, "words": "150-180",
     "prompt": "Some students are afraid of making mistakes in class. Write an "
               "essay arguing that failure can be useful for learning.",
     "hints": ["用一个具体例子开头", "分析失败为什么有价值", "给同学提一条建议"],
     "sample": 'In my first year of middle school I gave a class presentation that went badly. My voice shook, and I forgot half of what I had prepared. For weeks I was ashamed of that morning. Later, however, I realised that the presentation taught me more than any successful one. I learned that I needed to practise aloud instead of reading silently, and that a small card with three key words is enough to keep me going. Failure is useful only if we are willing to look at it honestly. For me, that presentation was the beginning of learning how to speak in public.'},
    {"topic": "City Life or Country Life", "genre": "议论文", "level": 1,
     "words": "120-150",
     "prompt": "Your class is debating where it is better to grow up. Write a "
               "passage stating and supporting your preference.",
     "hints": ["明确你的选择", "给出两条对比理由", "写出你的结论"],
     "sample": 'If I could choose where to grow up, I would choose the countryside. The air is cleaner, and a child can run outside without being watched every second. Space is also cheaper, so families can live in a house rather than a small flat. City life offers better schools and hospitals, and I admit this is a real disadvantage of my choice. But I believe that a quiet childhood, with trees and open sky, gives a person something that cannot be bought later. I would rather trade some convenience for that.'},
    {"topic": "An Unforgettable Lesson", "genre": "记叙文", "level": 2,
     "words": "80-100",
     "prompt": "Write about a class or an activity that taught you something you "
               "did not expect to learn.",
     "hints": ["交代背景", "写清楚发生了什么", "说明你学到了什么"],
     "sample": 'Last term our biology teacher took us to a pond behind the school. She asked us to write down everything we could see in ten minutes and then to compare our lists. Mine had eleven items; the longest list in the class had thirty-four. The teacher had said nothing about how to look. In that hour I learned that attention is a skill and that I had been walking past a great deal. I still use her method when I read a text I find difficult.'},
    {"topic": "Mobile Phones in Class", "genre": "应用文", "level": 1,
     "words": "120-150",
     "prompt": "Your school is reviewing its policy on mobile phones. Write a "
               "letter to the head teacher giving your opinion.",
     "hints": ["说明你的身份和写信目的", "给出理由", "提出一条具体建议"],
     "sample": "Dear Head Teacher, I am a student in Class Three, and I am writing about the school's rules on mobile phones. We understand why the rules exist: a phone in a pocket is a constant temptation. However, a complete ban creates new problems, because many of us travel a long way home and need to tell our parents when we arrive. I suggest a middle path: phones are collected at the start of each lesson and returned at the end, and they are allowed in the corridors and after school. This keeps the classroom quiet without taking away a tool our families depend on. Yours, Li Hua."},
    {"topic": "The Person I Admire", "genre": "记叙文", "level": 2, "words": "80-100",
     "prompt": "Your English teacher has asked the class to write about someone "
               "they admire.",
     "hints": ["介绍这个人是谁", "用一个事例说明原因", "说明他/她对你的影响"],
     "sample": 'The person I admire most is my grandmother. She never went to university, but she raised four children while running a small shop. What I admire is not the achievement itself but the way she did it. When I was twelve our family had a difficult year, and I never once heard her complain. She simply got up earlier. She now reads two newspapers a day and asks me about the words she does not know. Whenever I am tempted to say that something is too hard, I remember her at the counter at five in the morning.'},
]

#: 阅读材料（链路 ``question-gen``）。体裁要散开，别十条都长一个样。
BANK_READING = [
    {"topic": "睡眠", "level": 0,
     "text": "Sleep is not simply a period during which the body shuts down. "
             "During deep sleep, the brain replays the patterns of activity it "
             "produced while learning, and it stores the important parts as "
             "long-term memory. Students who sleep seven to nine hours remember "
             "more than those who stay up studying. The same research also shows "
             "that a single night without sleep reduces attention by about thirty "
             "percent, which is close to the effect of a small amount of alcohol."},
    {"topic": "城市绿化", "level": 1,
     "text": "Trees in a city do more than make it look pleasant. Their leaves "
             "catch dust, and their shade lowers the temperature of the street by "
             "several degrees in summer. A study of three Chinese cities found "
             "that streets with tall trees were up to four degrees cooler than "
             "streets without them. City planners now treat the age of a tree as "
             "an important number, because a tree takes decades to reach its full "
             "size."},
    {"topic": "在线课程", "level": 1,
     "text": "When online courses first appeared, many people believed they would "
             "replace universities. Two decades later the picture is more "
             "complicated. Online courses are excellent at delivering "
             "information, and they are cheap. What they still struggle with is "
             "keeping students motivated. Figures from several platforms show "
             "that fewer than one in ten students who start a free course finish "
             "it."},
    {"topic": "人工智能", "level": 0,
     "text": "Modern translation systems do not look up words one by one. They "
             "learn patterns from millions of pairs of sentences and then produce "
             "the most probable translation of a new sentence. This works "
             "surprisingly well for ordinary text. It fails, however, when a "
             "sentence depends on something outside itself, such as a joke that "
             "only works in the original language."},
    {"topic": "咖啡", "level": 0,
     "text": "Coffee was first grown in the highlands of Ethiopia. According to "
             "one well-known story, a shepherd noticed that his goats became "
             "unusually lively after eating the red berries of a certain bush. He "
             "took the berries to a nearby monastery, where the monks made a drink "
             "from them. Whatever the truth of the story, coffee had spread across "
             "the Arabian peninsula by the fifteenth century."},
    {"topic": "塑料回收", "level": 1,
     "text": "Recycling plastic is harder than putting a bottle into the right "
             "bin. Bottles are usually made of one kind of plastic, so they are "
             "easy to handle. Packaging, however, often mixes several materials "
             "that must be separated before anything can be reused. This is why "
             "many cities recycle bottles but send most other plastic to be "
             "burned or buried."},
    {"topic": "蜜蜂", "level": 1,
     "text": "Honeybees tell each other where food is by dancing. A returning "
             "worker performs a figure-of-eight movement on the surface of the "
             "honeycomb. The direction of the straight part of the dance, "
             "measured against gravity, points towards the flowers. The length of "
             "the dance tells the other bees how far away the food is."},
    {"topic": "太空探索", "level": 0,
     "text": "Sending a person to Mars raises problems that no previous mission "
             "has solved. The journey takes about seven months each way. During "
             "that time the crew cannot be supplied from Earth, so everything they "
             "need must be carried or produced on board. The greater difficulty, "
             "however, may be psychological: the crew will be isolated from "
             "everyone they know for years."},
    {"topic": "方言", "level": 1,
     "text": "A dialect is not an incorrect version of a standard language. It is "
             "a complete system with its own grammar and vocabulary, developed by "
             "the people who speak it. When a dialect disappears, the knowledge "
             "held in its words often disappears with it. For example, some "
             "fishing communities have separate names for dozens of kinds of wind, "
             "none of which exist in the standard language."},
    {"topic": "阅读习惯", "level": 1,
     "text": "Reading on a screen and reading on paper are not the same activity. "
             "In a study at a European university, students who read a text on "
             "paper remembered more of the details than students who read the same "
             "text on a screen. The researchers believe that the paper readers "
             "used the physical position of the text on the page as a memory aid, "
             "something a scrolling screen does not provide."},
]

#: 翻译练习（链路 ``translate-review``）。``tgt_ref`` 是**人工写死的**参考译文——
#: 内容库里不该有平台产出的痕迹。
BANK_TRANS_SENTENCES = [
    {"src_lang": "en", "tgt_lang": "zh", "focus": "时态", "level": 1,
     "src_text": "By the time we arrived at the station, the train had already left.",
     "tgt_ref": "我们到达车站时，火车已经开走了。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "被动语态", "level": 1,
     "src_text": "The bridge was rebuilt last year using materials from the old one.",
     "tgt_ref": "这座桥去年重建时使用了旧桥的材料。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "定语从句", "level": 0,
     "src_text": "She recommended a book that changed the way I think about work.",
     "tgt_ref": "她推荐了一本书，那本书改变了我对工作的看法。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "介词", "level": 1,
     "src_text": "The meeting has been put off until next Thursday.",
     "tgt_ref": "会议已经推迟到下周四。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "长句拆分", "level": 0,
     "src_text": "Although the experiment failed twice, the results still gave the "
                 "team useful information about what to avoid.",
     "tgt_ref": "尽管实验失败了两次，结果仍为团队提供了关于应当避免什么的有用信息。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "虚拟语气", "level": 0,
     "src_text": "If the weather had been better, we would have finished the survey "
                 "a week earlier.",
     "tgt_ref": "如果当时天气好一些，我们本可以提前一周完成这项调查。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "量词", "level": 1,
     "src_text": "A small piece of advice from a stranger changed my whole plan.",
     "tgt_ref": "陌生人的一句建议改变了我整个计划。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "情态动词", "level": 1,
     "src_text": "You needn't have brought anything to the dinner.",
     "tgt_ref": "你本来不必带任何东西来参加晚宴。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "名词性从句", "level": 0,
     "src_text": "What surprised me most was that nobody had noticed the error.",
     "tgt_ref": "最让我意外的是，居然没有人注意到这个错误。"},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "分词结构", "level": 0,
     "src_text": "Having lived in the city for ten years, she still preferred the "
                 "quiet of the countryside.",
     "tgt_ref": "在这座城市住了十年后，她仍然更喜欢乡下的安静。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "主谓一致", "level": 1,
     "src_text": "我们班每个学生都参加了这次志愿活动。",
     "tgt_ref": "Every student in our class took part in the volunteering activity."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "成语", "level": 0,
     "src_text": "他做事一向三思而后行。",
     "tgt_ref": "He always thinks twice before he acts."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "无主句", "level": 1,
     "src_text": "据报道，这项政策将于明年开始实施。",
     "tgt_ref": "It is reported that the policy will take effect next year."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "把字句", "level": 1,
     "src_text": "请把这封信转交给你的班主任。",
     "tgt_ref": "Please pass this letter on to your head teacher."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "时间状语", "level": 0,
     "src_text": "随着人工智能的发展，翻译工作正在发生巨大的变化。",
     "tgt_ref": "With the development of artificial intelligence, translation work "
                "is changing dramatically."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "并列结构", "level": 1,
     "src_text": "他不仅会弹钢琴，而且擅长画画。",
     "tgt_ref": "He can not only play the piano but is also good at painting."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "条件句", "level": 1,
     "src_text": "如果明天不下雨，我们就去爬山。",
     "tgt_ref": "If it does not rain tomorrow, we will go climbing."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "插入语", "level": 0,
     "src_text": "这个方案，据我所知，还没有经过测试。",
     "tgt_ref": "This plan, as far as I know, has not been tested yet."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "比较结构", "level": 1,
     "src_text": "这本书比我想象的有趣得多。",
     "tgt_ref": "This book is far more interesting than I expected."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "让步状语", "level": 0,
     "src_text": "尽管困难重重，项目还是按期完成了。",
     "tgt_ref": "Despite all the difficulties, the project was completed on time."},
]

BANK = {
    "passage_en": BANK_PASSAGE_EN,
    "essay_prompts": BANK_ESSAY_PROMPTS,
    "reading_passages": BANK_READING,
    "trans_sentences": BANK_TRANS_SENTENCES,
}

#: 用户可见的诚信说明，按链路分。写进任务卡的 ``notes``。
NOTES = {
    "tts-oral": [
        "TTS 合成的是**标准发音**，拿去评阅分数基本都偏高（实测 98.0）。",
        "这条任务的价值在「给学生一段范读」和「看 feedback 里该怎么读」，"
        "不在「测出学生多差」。",
        "第 2 步直接吃第 1 步的 audioUrl，不重新上传（口语评阅接受 http URL）。",
    ],
    "essay-review": [
        "第 1 步生成的是**题目**，不是范文——学生要自己写。",
        "别拿平台生成的文章去评阅：那只会拿高分，学生学不到东西。",
    ],
    "question-gen": [
        "**这条链路建的阅读材料删不掉**（平台没有 rm/delete）。",
        "默认走 preview：同步回题面、不落库、无 quesId，**零残留**。",
        "建材料那一步标成「可选」，默认不执行；要留档才加 `--all-steps`。",
        "⚠️ **「答题」那一步实测打不通**：``ques/ans`` 在两个主机上都 404，"
        "前端产物里也没有它——跟 op50 / `article` 那三个空壳端点同类。"
        "默认路线**不含答题**，答题交给学生自己做。",
    ],
    "translate-review": [
        "op36 **只打分，不产出译文**——返回的 translation 是你提交的那份的原样存档。",
        "语种码只认小写 en/zh，且 tgtLang 必须等于记录上的 langTo；写错不报错，只给假分。",
        "要评另一对语种必须新建记录，不要复用 wmId。",
        "`--auto` 会**拿第 1 步的参考译文当作学生的译文**（`local.take`，不碰平台）"
        "——那只是把 op36 这条链演示完，**不是「替学生翻了一遍」**。",
    ],
}

#: 各链路的步骤模板。``args`` 里的 ``{{slot:…}}`` 在**生成期**解析成最终值，
#: ``{{stepN:…}}`` 在**执行期**才解析（生成期只校验它不构成前向引用）。
CHAIN_STEPS = {
    "tts-oral": [
        {"kind": "app", "app": "speech", "method": "say", "label": "合成朗读音频",
         "args": {"text": "{{slot:passage}}", "speaker": "{{slot:speaker}}",
                  "language": 2, "speed": "{{slot:speed}}"},
         "produces": {"audioUrl": "音频地址（下一步直接吃）"},
         "expect": "拿到 audioUrl"},
        {"kind": "app", "app": "oral", "method": "review",
         "label": "口语评阅（打分 + 发音反馈）",
         "args": {"audio": "{{step1:audioUrl}}", "content": "{{slot:passage}}",
                  "ques_type": 1},
         "produces": {"evaluation.overall": "百分制总分",
                      "evaluation.feedback": "逐词/逐句发音建议"},
         "expect": "两步都成功；分数通常落在 80–100（标准音）"},
    ],
    "essay-review": [
        {"kind": "human", "label": "写一篇英语作文",
         "brief": "题目：{{slot:prompt}}\n"
                  "话题：{{slot:topic}}　体裁：{{slot:genre}}　"
                  "词数：{{slot:words}}　学段：{{slot:levelName}}\n"
                  "要点提示：{{slot:hints}}",
         "args": {},
         "produces": {"content": "学生写的正文"},
         "expect": "学生交上一篇正文",
         # 替身：素材库里**预写**的一篇范文（`local.take`，不碰平台）。
         #
         # ⚠️ **不要换成平台的写作能力**（`article/common_continue`）。实测它
         # 产不出作文：给一句题干做 `before`，回来的是**中文的写作建议**
         # （"1. 添加个人成长经历：…"），作文评阅直接回 `score=0` +
         # "没有足够的英文单词。"。`--auto` 要演示的是作文评阅，就得喂真英文。
         "substitute": {"app": "local", "method": "take",
                        "args": {"key": "content",
                                 "value": "{{slot:sample}}"}}},
        {"kind": "app", "app": "review", "method": "essay",
         "label": "作文评阅（总分 + 分项 + 逐句纠错）",
         "args": {"content": "{{step1:content}}", "topic": "{{slot:topic}}",
                  "level": "{{slot:levelCode}}"},
         "produces": {"score": "加权总分",
                      "contentScore": "内容分",
                      "correct[]": "逐句纠错与改写建议"},
         "expect": "拿到总分与逐句纠错"},
    ],
    "question-gen": [
        {"kind": "app?", "app": "questions", "method": "create_material",
         "label": "建阅读材料（**可选**：材料建了就删不掉）",
         "args": {"content": "{{slot:passage}}", "education": "{{slot:education}}" },
         "produces": {"rmId": "阅读材料 id"},
         "expect": "拿到 rmId（**这一步会在平台上留下不可删的材料**）"},
        {"kind": "app", "app": "questions", "method": "preview",
         "label": "试出题：{{slot:ployName}}（同步回题面，不落库、无 quesId）",
         "args": {"rm_id": "{{slot:rm_id}}", "ploy": "{{slot:ploy}}"},
         "produces": {"items[]": "题面（无 quesId，采纳不了）"},
         "expect": "拿到题面"},
        # ⚠️ 这一步**不是平台调用**：文档里的 `ques/ans` 实测 404（见 NOTES），
        # 所以答题只能人工。演示整条链时用 `local.take` 把题面原样带下去。
        {"kind": "human", "label": "答题（**平台没有可用的答题接口**，人工做）",
         "brief": "题目：{{step2:items.0.ques}}",
         "args": {},
         "produces": {"answer": "学生的作答"},
         "expect": "学生作答（这一档平台不判分）",
         "substitute": {"app": "local", "method": "take",
                        "args": {"key": "answer",
                                 "value": "{{step2:items.0.ques}}"}}},
    ],
    "translate-review": [
        {"kind": "app", "app": "translate", "method": "text",
         "label": "出参考译文（教师版可见，学生版隐藏）",
         "args": {"text": "{{slot:src_text}}", "from": "{{slot:src_lang}}",
                  "to": "{{slot:tgt_lang}}"},
         "student_hidden": True,
         "produces": {"translation": "参考译文"},
         "expect": "拿到参考译文"},
        {"kind": "human", "label": "自己翻一遍",
         "brief": "源文本（{{slot:src_langName}}）：{{slot:src_text}}\n"
                  "目标语种：{{slot:tgt_langName}}\n"
                  "考点：{{slot:focus}}",
         "args": {},
         "produces": {"tgt_text": "学生的译文"},
         "expect": "学生交上一份译文",
         # 替身：**把第 1 步的参考译文原样当作学生的译文**交给 op36。
         # 这不是"生成一份译文"（那是 `translate` 的活），只是让 `--auto`
         # 能把 op36 那条链演示完。`local.take` 不碰平台，零残留。
         "substitute": {"app": "local", "method": "take",
                        "args": {"key": "tgt_text",
                                 "value": "{{step1:translation}}"}}},
        {"kind": "app", "app": "tr", "method": "review",
         "label": "翻译评阅（**只打分，不产出译文**）",
         "args": {"src_text": "{{slot:src_text}}", "tgt_text": "{{step2:tgt_text}}",
                  "src_lang": "{{slot:src_lang}}", "tgt_lang": "{{slot:tgt_lang}}"},
         "produces": {"score": "百分制分数"},
         "expect": "拿到一个分数"},
    ],
}

#: 四条链路。``content_slots`` 里的键**定义"内容是否相同"**——参数维度
#: （音色 / 语速 / 策略）不参与，因为它们只让任务看起来不同，学生拿到的是同一道题。
CHAINS = {
    "tts-oral": {
        "name": "朗读 → 口语评阅",
        "scenario": "英语朗读测评：先合成一段标准范读音频，再交口语评阅打分",
        "bank": "passage_en",
        "content_slots": ["passage"],
        "title": "{title}",
        "tags": ["朗读", "发音", "口语评阅", "TTS"],
        "params": {"speaker": ["en_luka", "us_annie"],
                   "speed": [0.8, 1.0, 1.2]},
        "steps": CHAIN_STEPS["tts-oral"],
    },
    "essay-review": {
        "name": "作文题目 → 作文评阅",
        "scenario": "英语写作测评：先出题，学生写完交作文评阅打分",
        "bank": "essay_prompts",
        "content_slots": ["topic", "prompt"],
        "title": "英语写作：{topic}",
        "tags": ["写作", "作文评阅", "英语"],
        "params": {},
        "steps": CHAIN_STEPS["essay-review"],
    },
    "question-gen": {
        "name": "阅读材料 → 出题 → 答题",
        "scenario": "阅读理解练习：先给材料，再出题，再答题",
        "bank": "reading_passages",
        "content_slots": ["topic", "passage"],
        "title": "阅读理解练习：{topic}",
        "tags": ["阅读理解", "出题", "答题"],
        "params": {"ploy": [f"{code}:1" for code in sorted(PLOY_CODES)]},
        "steps": CHAIN_STEPS["question-gen"],
    },
    "translate-review": {
        "name": "翻译 → 翻译评阅",
        "scenario": "英汉互译练习：出参考译文，学生翻一遍，系统打分",
        "bank": "trans_sentences",
        "content_slots": ["src_text"],
        "title": "翻译练习（{src_langName} → {tgt_langName}）：{focus}",
        "tags": ["翻译", "翻译评阅", "英汉互译"],
        "params": {},
        "steps": CHAIN_STEPS["translate-review"],
    },
}

ALL_CHAINS = list(CHAINS)

_LEVEL_NAME = {v: k for k, v in Level.NAME.items()}   # "高中" -> 1
_LANG_NAME = {"en": "英语", "zh": "中文"}

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")
_STEP_REF = re.compile(r"step(\d+)")


# ----------------------------------------------------------------------
# 占位符
# ----------------------------------------------------------------------
def _dig(obj, path):
    """按点路径取值；``items.0.quesText`` 这种也认。取不到抛 :class:`AigcError`。"""
    if not path:
        return obj
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            if part not in cur:
                raise AigcError(f"取不到 {path!r}：字典里没有 {part!r}")
            cur = cur[part]
        elif isinstance(cur, (list, tuple)):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                raise AigcError(f"取不到 {path!r}：列表下标 {part!r} 不合法")
        else:
            raise AigcError(f"取不到 {path!r}：{type(cur).__name__} 不可下标")
    return cur


def _resolve_one(expr, ctx, step_index, *, phase):
    """解析一个占位符表达式。返回 ``(值, 是否已定值)``。

    支持四种来源：``slot:`` / ``stepN:`` / ``batch:`` / ``task:``，
    外加两个**取值域**写法 ``lang:`` 和 ``ploy:``（见下）。

    .. note::
       **为什么要有 ``lang:`` 而不是把两字母码直接写进步骤表**：步骤表是
       模块级常量，会被 ``dict(...)`` 浅拷贝出来复用；如果里面直接写
       ``{"foo": [], "bar": []}`` 这种可变值，多个任务会**共享同一个列表对象**。
       ``lang:`` / ``ploy:`` 把"取值域"和"任务状态"分开，值永远从常量里取，
       不落进任何会被改的容器。
    """
    head, _, rest = expr.strip().partition(":")
    head = head.strip()
    rest = rest.strip()

    if head == "slot":
        if rest not in ctx["slots"]:
            raise LocalCheckError(f"占位符 {{{{slot:{rest}}}}} 引用了不存在的槽位")
        return ctx["slots"][rest], True

    if head in ("batch", "task"):
        return ctx[head].get(rest), True

    if head == "lang":
        if rest not in _LANG_NAME:
            raise LocalCheckError(f"占位符 {{{{lang:{rest}}}}} 只认 "
                            f"{'/'.join(_LANG_NAME)}")
        return _LANG_NAME[rest], True

    if head == "ploy":
        code = rest.split(":")[0]
        if not code.isdigit() or int(code) not in PLOY_CODES:
            raise LocalCheckError(f"占位符 {{{{ploy:{rest}}}}} 的策略码不在 "
                            f"question_gen.PLOY_CODES 里")
        return PLOY_CODES[int(code)], True

    m = _STEP_REF.fullmatch(head)
    if m:
        n = int(m.group(1))
        if n >= step_index:
            # 前向引用：生成期就拒，不写进任务卡
            raise LocalCheckError(
                f"第 {step_index} 步引用了 {{{{step{n}:…}}}}——**前向引用**，"
                f"那一步还没跑，取不到值")
        if phase == "gen":
            # 执行期才解析，这里原样留着以便人能读懂任务卡
            return None, False
        src = ctx.setdefault("steps", {}).get(n)
        if not src:
            raise AigcError(f"第 {step_index} 步引用了第 {n} 步的产出，"
                            f"但那一步还没有输出")
        return _dig(src.get("output"), rest), True

    raise LocalCheckError(f"不认识的占位符 {{{{ {expr} }}}}"
                    f"（只支持 slot: / stepN: / batch: / task: / lang: / ploy:）")


def resolve(value, ctx, step_index, *, phase):
    """递归解析 ``value`` 里的占位符。``phase`` 是 ``"gen"`` 或 ``"run"``。

    生成期只有 ``{{slot:…}}`` 和 ``{{batch:…}}``/``{{task:…}}`` 会定值；
    ``{{stepN:…}}`` 留字面量（但**前向引用会被拒**）。
    """
    if isinstance(value, dict):
        return {k: resolve(v, ctx, step_index, phase=phase)
                for k, v in value.items()}
    if isinstance(value, list):
        return [resolve(v, ctx, step_index, phase=phase) for v in value]
    if not isinstance(value, str):
        return value

    # 整个字符串就是**一个**占位符时，原样返回那个值（保住类型）：
    # `"{{slot:speed}}"` 要还原成浮点 `1.0`，不是字符串 `"1.0"`
    # ——不然 `speed` 这类数字参数到不了接口，check 那一关也会误报。
    whole = _PLACEHOLDER.fullmatch(value)
    if whole:
        got, fixed = _resolve_one(whole.group(1), ctx, step_index, phase=phase)
        return got if fixed else value

    pieces, pos = [], 0
    for m in _PLACEHOLDER.finditer(value):
        pieces.append(value[pos:m.start()])
        got, fixed = _resolve_one(m.group(1), ctx, step_index, phase=phase)
        if fixed:
            pieces.append(str(got) if not isinstance(got, str) else got)
        else:
            pieces.append(m.group(0))      # 原样留着，执行期再解
        pos = m.end()
    pieces.append(value[pos:])
    out = "".join(pieces).strip()
    return out


def _check_forward_refs(steps):
    """生成期校验：任何一步都不许引用**它自己或后面**的步骤。"""
    for i, step in enumerate(steps, 1):
        seen = []
        _scan_steps(step.get("args"), seen)
        _scan_steps(step.get("brief"), seen)
        for expr in seen:
            head, _, _ = expr.partition(":")
            m = _STEP_REF.fullmatch(head.strip())
            if m and int(m.group(1)) >= i:
                raise LocalCheckError(
                    f"第 {i} 步引用了 {{{{step{m.group(1)}:…}}}}——**前向引用**，"
                    f"那一步还没跑，取不到值")


def _scan_steps(value, out):
    if isinstance(value, dict):
        for v in value.values():
            _scan_steps(v, out)
    elif isinstance(value, list):
        for v in value:
            _scan_steps(v, out)
    elif isinstance(value, str):
        out.extend(m.group(1) for m in _PLACEHOLDER.finditer(value))


def signature_of(slots, content_slots):
    """内容的"指纹"——**只有内容槽位参与**，参数不参与。

    两份任务卡的指纹相同 = 学生拿到的是同一道题，只是音色/语速不一样。
    """
    return json.dumps({k: slots.get(k) for k in sorted(content_slots)},
                      sort_keys=True, ensure_ascii=False)


# ----------------------------------------------------------------------
# 生成
# ----------------------------------------------------------------------
def _rows(chain):
    """链路素材库的每一行 → 一份内容槽位。"""
    return [dict(row) for row in BANK[chain["bank"]]]


def _fill_row(chain, row):
    """把素材行铺成完整的 ``slots``（内容 + 派生名 + 参数槽位）。

    ``row`` 是 ``BANK`` 里的一行——**浅拷贝**，所以下面凡是动过的地方一律
    **重新绑定**（``slots[x] = …``），不要 ``.append`` / ``.update`` 到行内
    已有的 list/dict 上，否则会改到 ``BANK`` 那个常量本身。
    """
    slots = dict(row)
    # 朗读短文的正文在素材库里叫 `text`，统一成 `passage`（链路的通用叫法）
    if "text" in slots and "passage" not in slots:
        slots["passage"] = slots.pop("text")
    slots["levelName"] = Level.NAME.get(slots.get("level"), "")
    slots["levelCode"] = slots.get("level", Level.COLLEGE)
    if "src_lang" in slots:
        slots["src_langName"] = _LANG_NAME.get(slots["src_lang"], slots["src_lang"])
        slots["tgt_langName"] = _LANG_NAME.get(slots["tgt_lang"], slots["tgt_lang"])
    if "prompt" in slots:
        slots["hints"] = "　".join(
            f"{i}. {h}" for i, h in enumerate(slots.get("hints") or [], 1))
    # 出题链路的 ``education`` 跟 ``constants.Level`` **不是一套编号**
    # （question_gen.EDUCATION 是 1 小学 / 2 初中 / 3 高中 / 4 职教 / 5 本科 /
    # 6 研究生 / 7 其他，而且**没有 0**）。所以按名字映射，别拿 level 直接用。
    level_name = slots.get("levelName")
    if level_name:
        slots["education"] = _EDUCATION_BY_NAME.get(level_name, 5)
    return slots


#: ``constants.Level`` 的名字 → ``question_gen.EDUCATION`` 的编号。
#: 两套编号**互不相通**（见 ``question_gen`` 的模块注释），只能按名字对。
#: 大学没有对应项（EDUCATION 没有 0），退到 5 本科。
_EDUCATION_BY_NAME = {
    "小学": 1,
    "初中": 2,
    "高中": 3,
    "大学": 5,
}


def _pick(rng, choices, i):
    """参数维度打散：**带上位置偏移**，避免同一批里参数全挤在一起。"""
    if not choices:
        return None
    return choices[(rng.randrange(len(choices)) + i) % len(choices)]


def gen_batch(chain_names, count, *, seed=None, rm_id=None):
    """生成一个批次。返回 ``(batch_id, tasks, manifest)``。**零平台调用。**"""
    if chain_names in (None, "all", ["all"]):
        chains = list(CHAINS)
    else:
        chains = list(chain_names)
    for name in chains:
        if name not in CHAINS:
            raise LocalCheckError(f"没有这条链路：{name!r}。可选："
                            f"{', '.join(ALL_CHAINS)}，或 'all'")
    if count < 1:
        raise LocalCheckError("--count 至少是 1")

    # ⚠️ 前向引用**先整体校验一遍**，再开始生成——否则跑到一半才抛错，
    #    调用方已经拿到半截结果了。
    for name in chains:
        _check_forward_refs(CHAINS[name]["steps"])

    rng = random.Random(seed)
    batch_id = time.strftime("%Y%m%d-%H%M%S") + "-%04x" % rng.getrandbits(16)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    tasks, used = [], set()
    for chain_name in chains:
        chain = CHAINS[chain_name]

        pool = [_fill_row(chain, row) for row in _rows(chain)]
        rng.shuffle(pool)

        # 先把内容槽位定下来——**它才是"内容是否相同"的判据**，
        # 参数（音色/语速/策略）不参与，因为参数只让任务看起来不同。
        picked = []
        for slots in pool:
            sig = signature_of(slots, chain["content_slots"])
            if sig in used:
                continue
            used.add(sig)
            picked.append(slots)
            if len(picked) == count:
                break
        if len(picked) < count:
            raise LocalCheckError(
                f"链路 {chain_name} 的素材只有 {len(pool)} 组，凑不出 {count} 条"
                f"内容不重复的任务——请减少 --count，或给 BANK 补素材")

        # 到这里才打散参数维度。每个任务的 slots 是**新建的 dict**，
        # 绝不共享可变对象（共享会在第二次赋值时改到别的任务上）。
        for i, slots in enumerate(picked, 1):
            full = dict(slots)
            full["rm_id"] = rm_id
            if chain_name == "question-gen":
                # 没绑材料时给个占位，好让任务卡里那一步的参数长什么样看得见；
                # 真跑之前必须用 --rm-id 重新生成（check_task 会拦）。
                full.setdefault("rm_id", rm_id)
            for key, choices in chain["params"].items():
                full[key] = _pick(rng, choices, i)
            if full.get("ploy"):
                code = str(full["ploy"]).split(":")[0]
                full["ployName"] = PLOY_CODES.get(int(code), "")
            task_id = f"EX-{batch_id}-{len(tasks) + 1:03d}"
            tasks.append(_build_task(chain_name, chain, task_id, batch_id, full))

    manifest = {
        "batchId": batch_id,
        "generatedAt": generated_at,
        "seed": seed,
        "count": len(tasks),
        "chains": {name: sum(1 for t in tasks if t["chain"] == name)
                   for name in chains},
        # 自证字段：必须等于 count，否则 gen 早就抛错了。有它用户不必自己比对。
        "distinctSignatures": len({t["signature"] for t in tasks}),
        "tasks": [{"taskId": t["taskId"], "chain": t["chain"],
                   "title": t["title"], "signature": t["signature"]}
                  for t in tasks],
    }
    return batch_id, tasks, manifest


def _build_task(chain_name, chain, task_id, batch_id, slots):
    ctx = {"slots": slots, "batch": {"id": batch_id}, "task": {"id": task_id}}
    content_slots = chain["content_slots"]

    steps = []
    for i, tpl in enumerate(chain["steps"], 1):
        step = {
            "n": i,
            "kind": tpl["kind"],
            "label": resolve(tpl["label"], ctx, i, phase="gen"),
            "expect": resolve(tpl.get("expect", ""), ctx, i, phase="gen"),
            "produces": tpl.get("produces") or {},
        }
        for key in ("app", "method", "student_hidden"):
            if tpl.get(key) is not None:
                step[key] = tpl[key]
        if tpl.get("brief"):
            step["brief"] = resolve(tpl["brief"], ctx, i, phase="gen")
        if "substitute" in tpl:
            sub = tpl["substitute"]
            step["substitute"] = {
                "app": sub["app"], "method": sub["method"],
                "args": resolve(sub.get("args") or {}, ctx, i, phase="gen"),
            }
        if tpl.get("args"):
            step["args"] = resolve(tpl["args"], ctx, i, phase="gen")
        steps.append(step)

    preconditions = []
    if chain_name == "question-gen" and not slots.get("rm_id"):
        preconditions.append(
            "这一批没有绑阅读材料（`--rm-id` 没给）。跑之前先 `exercise "
            "materials` 拿一个 rmId，再用 `--rm-id <id>` 重新生成。")

    title = chain["title"].format(
        **{k: (v if v is not None else "") for k, v in slots.items()})
    return {
        "taskId": task_id,
        "batchId": batch_id,
        "chain": chain_name,
        "chainName": chain["name"],
        "scenario": chain["scenario"],
        "title": title,
        "level": slots.get("level", Level.COLLEGE),
        "levelName": slots.get("levelName", ""),
        "tags": list(chain["tags"]),
        "signature": signature_of(slots, content_slots),
        "slots": slots,
        "steps": steps,
        "preconditions": preconditions,
        "notes": list(NOTES.get(chain_name, [])),
        "residue": residue_of(chain_name),
    }


def residue_of(chain_name):
    return {
        "tts-oral": "speech 记录 1 条 + wm 记录 1 条。清理："
                    "`speech delete <id>` / `oral delete <wmId>`"
                    "（两条都不带 `--yes` 只列不删）。",
        "essay-review": "wm 记录 1 条（清理：`oral`/`tr` 那一族之外，作文评阅走 "
                        "`review` 的记录；见 docs/call-chains.md §2）。"
                        "用了 `--auto` 还会多 1 条 article 记录。",
        "question-gen": "默认路线（`preview`）**零残留**；"
                        "加 `--all-steps` 建了材料则**永久留存**"
                        "（平台没有 `rm/delete`）。",
        "translate-review": "translate 记录 1 条（**type=1**——`cleanup` 会同时扫"
                            "`includeText` 的两种取值，所以看得见它；"
                            "清：`cleanup --ids <id> --yes`）+ wm 记录 1 条"
                            "（清：`tr delete <wmId>`）。",
    }.get(chain_name, "")


# ----------------------------------------------------------------------
# 落盘
# ----------------------------------------------------------------------
def root_dir(out=None):
    """批次根目录。默认 ``~/.cache/unipus-aigc/exercise``。"""
    if out:
        return os.path.abspath(os.path.expanduser(out))
    base = config.cache_dir()
    if not base:
        raise AigcError("主目录不可用，无法确定落盘位置——请显式给 --out")
    return os.path.join(base, "exercise")


def write_batch(batch_id, tasks, manifest, *, out=None):
    """写 ``manifest.json`` + ``tasks/NN-<taskId>.json`` + ``.md``。返回目录。"""
    dest = os.path.join(root_dir(out), batch_id)
    tdir = os.path.join(dest, "tasks")
    os.makedirs(tdir, exist_ok=True)
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    for i, task in enumerate(tasks, 1):
        stem = f"{i:03d}-{task['taskId']}"
        _write_json(os.path.join(tdir, stem + ".json"), task)
        with open(os.path.join(tdir, stem + ".md"), "w", encoding="utf-8") as fh:
            fh.write(render_markdown(task))
    return dest


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=False)
        fh.write("\n")


def batches(out=None):
    """已有的批次目录（新→旧）。"""
    root = root_dir(out)
    if not os.path.isdir(root):
        return []
    names = [n for n in os.listdir(root)
             if os.path.isfile(os.path.join(root, n, "manifest.json"))]
    return sorted(names, reverse=True)


def load_batch(batch_id, *, out=None):
    path = os.path.join(root_dir(out), batch_id, "manifest.json")
    if not os.path.isfile(path):
        raise AigcError(f"找不到批次 {batch_id}（{path}）")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def find_task_file(task_id, *, out=None):
    """按 ``taskId`` 找到任务卡的 ``.json`` 路径，跨批次。

    ``taskId`` 允许给**片段**（``EX-20260922-…-007`` 或更短），
    但匹配到多条时会报错而不是随便挑一条。
    """
    root = root_dir(out)
    if not os.path.isdir(root):
        raise AigcError(f"还没有任何批次（{root}）——先跑 `exercise gen`")
    hits = []
    for batch in sorted(os.listdir(root)):
        tdir = os.path.join(root, batch, "tasks")
        if not os.path.isdir(tdir):
            continue
        for name in sorted(os.listdir(tdir)):
            if name.endswith(".json") and not name.endswith(".run.json") \
                    and task_id in name:
                hits.append(os.path.join(tdir, name))
    if not hits:
        raise AigcError(f"找不到任务 {task_id}（在 {root} 下扫过）")
    if len(hits) > 1:
        raise AigcError(f"{task_id} 匹配到 {len(hits)} 条任务——请给完整的 taskId："
                        + "、".join(os.path.basename(h)[:-5] for h in hits[:5]))
    return hits[0]


def load_task(task_id, *, out=None):
    with open(find_task_file(task_id, out=out), encoding="utf-8") as fh:
        return json.load(fh)


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------
def render_markdown(task, *, student=False):
    """人类可读的任务卡。``student=True`` 时隐去标了 ``student_hidden`` 的步骤。"""
    lines = [f"# {task['title']}", ""]
    lines.append(f"- 任务号：`{task['taskId']}`")
    lines.append(f"- 链路：{task['chainName']}（`{task['chain']}`）")
    lines.append(f"- 场景：{task['scenario']}")
    lines.append(f"- 学段：{task['levelName'] or '—'}")
    lines.append(f"- 标签：{'、'.join(task['tags'])}")
    if student:
        lines.append("- 版本：**学生版**（隐藏了教师步骤）")
    lines.append("")

    if task.get("preconditions"):
        lines.append("> ⚠️ **跑之前要先满足：**")
        for p in task["preconditions"]:
            lines.append(f"> - {p}")
        lines.append("")

    lines.append("## 步骤")
    lines.append("")
    for step in task["steps"]:
        if student and step.get("student_hidden"):
            continue
        kind = {"app": "平台调用", "app?": "可选（默认不执行）",
                "human": "人工"}[step["kind"]]
        lines.append(f"### 第 {step['n']} 步 · {step['label']}")
        lines.append("")
        lines.append(f"*类型：{kind}*")
        if step.get("app"):
            lines.append(f"*调用：`{step['app']}.{step['method']}`*")
        lines.append("")
        if step.get("brief"):
            lines.append("```text")
            lines.append(step["brief"])
            lines.append("```")
            lines.append("")
        if step.get("args"):
            lines.append("参数：")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(step["args"], ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")
        if step.get("produces"):
            lines.append("产出：" + "、".join(f"`{k}`" for k in step["produces"]))
            lines.append("")
        if step.get("expect"):
            lines.append(f"期望：{step['expect']}")
            lines.append("")

    lines.append("## 说明")
    lines.append("")
    for note in task["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("## 平台残留")
    lines.append("")
    lines.append(task["residue"] or "—")
    lines.append("")
    return "\n".join(lines)


def chains_table():
    """``exercise chains`` 的正文。**

    ``label`` 里的 ``{{slot:ployName}}`` 之类**故意不解析**：链路概览讲的是
    "这一步做什么"，每一条任务卡里才是具体值。所以这里也会替换掉
    ``**加粗**`` 标记——CLI 输出是纯文本。"""
    rows = []
    for name in ALL_CHAINS:
        chain = CHAINS[name]
        rows.append(f"{name}\n  {chain['name']} —— {chain['scenario']}")
        for i, step in enumerate(chain["steps"], 1):
            kind = {"app": "平台", "app?": "平台（可选）",
                    "human": "人工"}[step["kind"]]
            label = _plain(step["label"])
            rows.append(f"    {i}. [{kind}] {label}")
        rows.append("")
    return "\n".join(rows).rstrip()


def _plain(text):
    """去掉 ``**加粗**`` 和 ``{{…}}`` 占位符，供纯文本输出用。

    ``{{slot:ployName}}`` → ``ployName``：链路概览讲的是"这一步按哪个槽位取值"，
    每条任务卡里才是具体值，所以这里保留槽位名反而更有用（跟 ``docs`` 里的
    步骤表写法一致）。
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    return _PLACEHOLDER.sub(
        lambda m: m.group(1).strip().split(":", 1)[-1].strip(), text)


# ----------------------------------------------------------------------
# 校验（全离线）
# ----------------------------------------------------------------------
def check_task(task):
    """逐条验任务卡。返回问题列表（空列表 = 通过）。**不碰网络。**"""
    problems = []
    chain = CHAINS.get(task["chain"])
    if chain is None:
        return [f"未知链路 {task['chain']!r}"]

    slots = task["slots"]
    if signature_of(slots, chain["content_slots"]) != task["signature"]:
        problems.append("signature 与 slots 对不上")

    for step in task["steps"]:
        n, app, method = step["n"], step.get("app"), step.get("method")
        if app is None:
            continue
        if (app, method) not in _KNOWN_CALLS:
            problems.append(f"第 {n} 步调的 {app}.{method} 不是已知的应用方法")
            continue
        problems += _check_args(step, slots)

    if task["chain"] == "question-gen" and not slots.get("rm_id"):
        problems.append("没有绑阅读材料（rm_id 为空）——跑之前要用 --rm-id 重新生成")

    raw = _find_unresolved(task["steps"])
    for expr in raw:
        head, _, _ = expr.partition(":")
        if not _STEP_REF.fullmatch(head.strip()):
            problems.append(f"占位符 {{{{ {expr} }}}} 没解析出来")
    return problems


#: 本地校验只覆盖**枚举型**参数——它们写错了平台会安静地给错结果或直接失败，
#: 而失败在好几条链路上是**照样建记录**的。
#:
#: ⚠️ **有意不在这里构造 client**：``speech`` 的音色、``questions`` 的阅读材料
#: 在平台侧是活的（白名单/数据），拿它们做校验就等于让 ``check`` 去发请求。
#: 因此：
#: * ``speaker`` 用的是**模块级白名单**（``SpeechAPI.SPEAKER_PARAMS`` 是类属性，
#:   不碰网络），这条是真校验；
#: * ``image`` 的 style/size 白名单**必须现取**，本函数**故意不查**——
#:   真实校验归执行器（``exercise run`` 在发请求前调 ``ImageGenAPI.check``）。
def _check_args(step, slots):
    """按各应用自己的白名单/取值域验参数。**全离线。**"""
    out = []
    a = step.get("args") or {}
    n = step["n"]
    if step["app"] == "speech":
        if a.get("speaker") not in SpeechAPI.SPEAKER_PARAMS:
            out.append(f"第 {n} 步的 speaker={a.get('speaker')!r} 不在音色白名单里")
        if a.get("language") not in (1, 2):
            out.append(f"第 {n} 步的 language={a.get('language')!r} 只能是 1/2")
        if a.get("speed") not in (0.8, 1.0, 1.2):
            out.append(f"第 {n} 步的 speed={a.get('speed')!r} 不在 {0.8, 1.0, 1.2} 里")
    elif step["app"] == "oral":
        if not a.get("content"):
            out.append(f"第 {n} 步的 content（朗读原文）不能空——"
                       f"它是 evaluationContent，实测必填")
        if a.get("ques_type") != 1:
            out.append(f"第 {n} 步的 ques_type 必须是 1（唯一的已知取值）")
        if not a.get("audio"):
            out.append(f"第 {n} 步的 audio 不能空")
    elif step["app"] == "review":
        if a.get("level") not in Level.NAME:
            out.append(f"第 {n} 步的 level={a.get('level')!r} 不在 constants.Level 里")
        if not a.get("content"):
            out.append(f"第 {n} 步的 content（作文正文）不能空")
    elif step["app"] == "tr":
        for key in ("src_lang", "tgt_lang"):
            if a.get(key) not in SUPPORTED_LANGS:
                out.append(f"第 {n} 步的 {key}={a.get(key)!r} 只能是 "
                           f"{'/'.join(SUPPORTED_LANGS)}")
        if a.get("src_lang") == a.get("tgt_lang"):
            out.append(f"第 {n} 步的源语种和目标语种不能相同")
    elif step["app"] == "questions":
        if step["method"] == "preview" and a.get("ploy"):
            code = str(a["ploy"]).split(":")[0]
            if code.isdigit() and int(code) not in PLOY_CODES:
                out.append(f"第 {n} 步的 ploy={a['ploy']!r} 的策略码不在 PLOY_CODES 里")
        if step["method"] == "create_material":
            if a.get("education") not in EDUCATION:
                out.append(f"第 {n} 步的 education={a.get('education')!r} 不在 "
                           f"question_gen.EDUCATION 里")
    return out


def _find_unresolved(value, out=None):
    out = [] if out is None else out
    if isinstance(value, dict):
        for v in value.values():
            _find_unresolved(v, out)
    elif isinstance(value, list):
        for v in value:
            _find_unresolved(v, out)
    elif isinstance(value, str):
        out.extend(m.group(1) for m in _PLACEHOLDER.finditer(value))
    return out


#: 执行器允许调的 (app, method) —— 只调**已有的应用方法**，不新写接口调用。
_KNOWN_CALLS = {
    ("speech", "say"), ("speech", "submit"),
    ("oral", "review"), ("oral", "submit"),
    ("review", "essay"), ("review", "submit_essay"),
    ("tr", "review"), ("tr", "submit"),
    ("questions", "create_material"), ("questions", "preview"),
    ("questions", "generate"),
    # **故意没有 ("questions", "answer")**：文档里的 `ques/ans` 实测 404

    ("translate", "text"),
    ("article", "common_continue"),
    ("image", "draw_and_wait"),
    ("local", "take"),        # 不碰平台：把上一步的产出原样搬成这一步的输入
}
