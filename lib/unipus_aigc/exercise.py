# -*- coding: utf-8 -*-
"""任务生成：产出一套**像真实教学任务**的任务清单。

.. note::
   **这个模块不调用平台。** 它只生成任务清单（markdown + json），一条网络
   请求都不发；任务本身交给 ``guide`` 路由到对应应用去执行。所以没配凭证
   也能用它出题。

一条**任务**由两部分组成：

* **素材准备**（``materials``）—— 这条任务要用到的东西从哪来。可以交给平台
   应用生成（比如口语评测的示范音频交给 ``speech`` 合成、阅读材料交给
   ``text-gen`` 起草），也可以是学生自己做的（朗读录音、作文正文、译文）。
* **执行**（``steps``）—— 把任务交给**一个应用**去办，配好参数。

比如"口语评测"这条任务：

```text
素材 1  示范音频        -> speech（TTS 合成）
素材 2  学生朗读录音    -> 学生自己录
执行    口语评阅        -> oral-review
```

.. warning::
   **这里没有"链路"，也没有"第几步跑什么"。** 早期版本把它做成"跨应用的有序
   链路"（TTS → 口语评阅），那是错的：任务是**交给一个应用执行**的东西，
   多出来的应用只是在**准备素材**。两条线（素材准备 / 执行）分开写。

.. warning::
   任务名要像真题，**不能是"XXX 测试"**。用户的原话是"任务要像真实任务"。
   所以库里的素材是按真实教学场景写的（一篇短文、一道作文题、一段听力脚本），
   标题也是按场景起的（"英语朗读评测：A Quiet Bookshop"），不是
   "TTS 测试 / 出题测试"。
"""

import json
import os
import random
import re
import time

from . import config
from .constants import Level
from .errors import AigcError
from .question_gen import PLOY_CODES


class LocalCheckError(AigcError):
    """本地校验没过——**一个请求都没发**。CLI 把它映射成退出码 2（用法错误）。

    链路名写错、素材不够、名字不合法都属于这一档。它们跟"平台出错"必须分开，
    否则用户会以为平台坏了。
    """


# ======================================================================
# 素材库
# ======================================================================
#
# 本地静态素材，**不调平台**：快、可复现、零残留。
#
# 为什么不用平台的文本生成现造素材：它慢（每条几秒）、**不可复现**（同一个
# seed 两次跑内容不一样）、而且要 token。平台那个能力用在**任务的素材步**里
# （见 TASKS 里的 text-gen 条目），而不是用来铺满这里。

#: 英文朗读短文。要**适合朗读**：长短句交错，句子不太长，题材分散。
BANK_PASSAGE_EN = [
    {"title": "A Quiet Morning", "level": 1, "topic": "校园生活",
     "text": "The library opens at eight in the morning. Students arrive early to "
             "find a seat by the window. Some read quietly, while others work on "
             "their laptops. The smell of coffee fills the air, and the morning "
             "passes peacefully."},
    {"title": "The School Garden", "level": 1, "topic": "校园生活",
     "text": "Our school has a small garden behind the science building. Every "
             "spring, students plant vegetables and flowers there. We take turns "
             "watering them during the lunch break. By summer, the whole garden is "
             "full of colour."},
    {"title": "Learning Online", "level": 0, "topic": "教育",
     "text": "Online courses have changed the way we learn. We can now attend "
             "lectures from anywhere in the world. However, staying focused at home "
             "is not always easy. Many students find that a fixed schedule helps a "
             "great deal."},
    {"title": "A Letter to My Teacher", "level": 1, "topic": "师生",
     "text": "Dear Miss Chen, thank you for helping me with my English last term. "
             "I used to be afraid of speaking in class. You always encouraged me to "
             "try again, even when I made mistakes. I have made real progress "
             "because of your patience."},
    {"title": "The Longest Journey", "level": 0, "topic": "旅行",
     "text": "Last summer, I travelled to the mountains with my family. The road "
             "was narrow and the weather changed very quickly. We walked for six "
             "hours before we reached the top. The view made every single step "
             "worthwhile."},
    {"title": "Recycling at Home", "level": 1, "topic": "环保",
     "text": "Recycling starts with small habits at home. We keep three boxes in "
             "the kitchen for paper, plastic and glass. My brother thought it was "
             "boring at first. Now he reminds the whole family to sort the rubbish "
             "every evening."},
    {"title": "The Science Fair", "level": 1, "topic": "科学",
     "text": "Our class built a model of the water cycle for the science fair. We "
             "used a plastic bottle, some cotton wool and a small lamp. When the "
             "lamp was switched on, drops of water appeared on the inside of the "
             "bottle. Everyone clapped."},
    {"title": "Working from Home", "level": 0, "topic": "工作",
     "text": "Working from home sounds comfortable, but it has its own "
             "difficulties. The line between work and rest becomes unclear. Some "
             "people finish their tasks very late at night. Others learn to close "
             "the laptop at six and go for a walk."},
    {"title": "A Trip to the Museum", "level": 1, "topic": "文化",
     "text": "The city museum is free on the first Sunday of every month. My "
             "father took me there to see an exhibition about ancient writing. I "
             "was surprised to learn that people once wrote on animal bones. We "
             "stayed until the guards asked us to leave."},
    {"title": "The New Student", "level": 1, "topic": "校园生活",
     "text": "A new student joined our class this week. Her name is Lily, and she "
             "moved here from another city. At first, she sat alone and said very "
             "little. Yesterday, she raised her hand and answered a difficult "
             "question. Everyone turned to look at her."},
    {"title": "Why We Sleep", "level": 0, "topic": "科学",
     "text": "Scientists still do not fully understand why we sleep. What they do "
             "know is that the brain uses this time to organise what we learned "
             "during the day. Students who sleep well remember more than those who "
             "study all night."},
    {"title": "The Village Market", "level": 1, "topic": "生活",
     "text": "Every Saturday, the village market opens before sunrise. Farmers "
             "bring vegetables that were picked the evening before. My grandmother "
             "always buys the same kind of bread from the same woman. They have "
             "known each other for forty years."},
    {"title": "A Difficult Choice", "level": 0, "topic": "成长",
     "text": "Choosing a university is harder than it looks. Some students follow "
             "the advice of their parents, and others follow their friends. The "
             "most useful thing I did was to visit three campuses and talk to "
             "students there. That changed my mind completely."},
    {"title": "Volunteering on Sundays", "level": 1, "topic": "公益",
     "text": "Every Sunday morning, a group of us clean the river bank near the "
             "old bridge. We wear thick gloves and carry large bags. Last month we "
             "collected more than thirty kilograms of plastic. The birds have come "
             "back to that part of the river."},
    {"title": "The Old Bookshop", "level": 0, "topic": "文化",
     "text": "There is an old bookshop at the end of our street. The owner knows "
             "every shelf by heart. If you describe a book you cannot remember, he "
             "will usually find it within a minute. He has been running the shop "
             "since he was twenty-one."},
    {"title": "Learning to Cook", "level": 1, "topic": "生活",
     "text": "My mother decided that I should learn to cook before I left home. "
             "We started with simple things like rice and soup. The first three "
             "attempts were terrible. By the end of the month, I could prepare a "
             "meal for four people."},
    {"title": "The Night Train", "level": 0, "topic": "旅行",
     "text": "The night train to the coast leaves at eleven. There are only four "
             "passengers in my carriage: an old couple, a student with a guitar, "
             "and me. Nobody speaks. Somewhere after midnight the student begins "
             "to play, very quietly, and the old man hums along."},
    {"title": "What the River Carries", "level": 0, "topic": "环保",
     "text": "Every spring the river carries branches, leaves and, sadly, a great "
             "deal of plastic down to the sea. Volunteers wait at the bend with "
             "nets and pull out what they can. Last year they collected four "
             "tonnes. It is not enough, but it is not nothing either."},
    {"title": "The Interview", "level": 1, "topic": "成长",
     "text": "My first interview lasted twelve minutes. I had prepared for two "
             "hours. The manager asked one question I had not expected: what would "
             "you do if you disagreed with your team leader? I answered honestly, "
             "and she smiled for the first time."},
    {"title": "A Small Kindness", "level": 1, "topic": "生活",
     "text": "On a crowded bus last winter, an old woman dropped her bag and "
             "everything fell out. Three strangers knelt down at once to help. "
             "When the bus stopped, nobody had said a word to anybody else. I "
             "thought about that for the rest of the day."},
]

#: 英语作文题。每条都带**交际情境**和内容要点——真实作文题都长这样，
#: 光给一个话题不像题。
BANK_ESSAY_PROMPTS = [
    {"topic": "Online Learning", "genre": "议论文", "level": 1, "words": "120-150",
     "prompt": "Your school newspaper is collecting opinions on online learning. "
               "Write an article discussing whether online classes can replace "
               "traditional classrooms.",
     "hints": ["说明线上学习的两个优势", "指出至少一个明显不足", "给出你的结论"]},
    {"topic": "A Memorable Trip", "genre": "记叙文", "level": 2, "words": "80-100",
     "prompt": "Your English club is publishing a collection of short travel "
               "stories. Write about a trip that you still remember clearly.",
     "hints": ["交代时间地点和同行的人", "写一件具体发生的事", "说明为什么至今难忘"]},
    {"topic": "Protecting the Environment", "genre": "议论文", "level": 1,
     "words": "120-150",
     "prompt": "The city council is asking students for ideas on reducing plastic "
               "waste. Write a proposal explaining what your school could do.",
     "hints": ["描述目前的一个具体问题", "提出两条可执行的建议", "说明预期效果"]},
    {"topic": "My Ideal Job", "genre": "说明文", "level": 2, "words": "80-100",
     "prompt": "Your class is preparing a career day. Write a short passage "
               "introducing the job you would most like to do in the future.",
     "hints": ["说明这是什么工作", "写出你选择它的两个理由", "谈谈需要做什么准备"]},
    {"topic": "Reading Habits", "genre": "图表作文", "level": 0, "words": "150-180",
     "prompt": "A recent survey shows that students spend far more time on short "
               "videos than on books. Write an essay analysing this situation.",
     "hints": ["概括调查反映的趋势", "分析两个可能的原因", "提出你的看法"]},
    {"topic": "A Letter of Thanks", "genre": "应用文", "level": 1, "words": "80-100",
     "prompt": "Write a letter to a teacher who helped you overcome a difficulty "
               "in your studies.",
     "hints": ["说明写信的目的", "具体写他/她帮了你什么", "表达感谢并说明你的变化"]},
    {"topic": "Technology and Friendship", "genre": "议论文", "level": 0,
     "words": "150-180",
     "prompt": "Some people say social media brings friends closer; others say it "
               "makes real friendship weaker. Write an essay giving your view.",
     "hints": ["呈现两种对立观点", "用一个例子支持你的立场", "承认对方观点合理之处"]},
    {"topic": "Volunteering", "genre": "记叙文", "level": 1, "words": "120-150",
     "prompt": "Your school is recruiting volunteers for a community service "
               "programme. Write about a volunteering experience of your own.",
     "hints": ["说明你做了什么", "写一个遇到的困难", "写出你的收获"]},
    {"topic": "Healthy Eating", "genre": "说明文", "level": 2, "words": "80-100",
     "prompt": "Your school clinic is publishing a guide on healthy eating for "
               "teenagers. Write a short passage for it.",
     "hints": ["指出中学生饮食的一个常见问题", "给出两条具体建议", "提醒坚持的重要性"]},
    {"topic": "The Value of Failure", "genre": "议论文", "level": 0, "words": "150-180",
     "prompt": "Some students are afraid of making mistakes in class. Write an "
               "essay arguing that failure can be useful for learning.",
     "hints": ["用一个具体例子开头", "分析失败为什么有价值", "给同学提一条建议"]},
    {"topic": "City Life or Country Life", "genre": "议论文", "level": 1,
     "words": "120-150",
     "prompt": "Your class is debating where it is better to grow up. Write a "
               "passage stating and supporting your preference.",
     "hints": ["明确你的选择", "给出两条对比理由", "写出你的结论"]},
    {"topic": "An Unforgettable Lesson", "genre": "记叙文", "level": 2,
     "words": "80-100",
     "prompt": "Write about a class or an activity that taught you something you "
               "did not expect to learn.",
     "hints": ["交代背景", "写清楚发生了什么", "说明你学到了什么"]},
    {"topic": "Mobile Phones in Class", "genre": "应用文", "level": 1,
     "words": "120-150",
     "prompt": "Your school is reviewing its policy on mobile phones. Write a "
               "letter to the head teacher giving your opinion.",
     "hints": ["说明你的身份和写信目的", "给出理由", "提出一条具体建议"]},
    {"topic": "The Person I Admire", "genre": "记叙文", "level": 2, "words": "80-100",
     "prompt": "Your English teacher has asked the class to write about someone "
               "they admire.",
     "hints": ["介绍这个人是谁", "用一个事例说明原因", "说明他/她对你的影响"]},
]

#: 阅读材料选题。**只给要点，正文交给 ``text-gen`` 起草**——这正是
#: "创造一条出题任务，可以用文本生成先写一篇阅读材料"。
BANK_READING_BRIEFS = [
    {"topic": "睡眠与记忆", "level": 0, "education": 5, "genre": "科普说明文",
     "opening": "Sleep is not simply a period during which the body shuts down.",
     "closing": "That is why a regular bedtime matters more than an extra hour of "
                "late-night study.",
     "points": ["深睡时大脑会重放白天学到的活动模式",
                "睡眠充足的学生记得更多",
                "一夜不睡会让注意力下降约三成"]},
    {"topic": "城市绿化", "level": 1, "education": 3, "genre": "说明文",
     "opening": "Trees in a city do more than make it look pleasant.",
     "closing": "City planners now treat the age of a tree as an important number.",
     "points": ["树叶拦尘埃", "树荫能让街面低好几度", "一棵树要几十年才长成"]},
    {"topic": "在线课程", "level": 1, "education": 3, "genre": "议论文",
     "opening": "When online courses first appeared, many people believed they "
                "would replace universities.",
     "closing": "The real question is not whether to use them, but how to keep "
                "students going.",
     "points": ["在线课程擅长传递信息、成本低", "难点是保持学生的动力",
                "免费课程的完成率不到一成"]},
    {"topic": "机器翻译", "level": 0, "education": 5, "genre": "科普说明文",
     "opening": "Modern translation systems do not look up words one by one.",
     "closing": "That is where a human translator is still hard to replace.",
     "points": ["从海量句对里学模式", "普通文本上表现很好",
                "依赖上下文的句子（比如双关）会翻错"]},
    {"topic": "咖啡的来历", "level": 0, "education": 5, "genre": "记叙说明文",
     "opening": "Coffee was first grown in the highlands of Ethiopia.",
     "closing": "The rest of the story belongs to the traders.",
     "points": ["牧羊人发现山羊吃了红果子变精神", "修士用果子做了饮料",
                "十五世纪已传遍阿拉伯半岛"]},
    {"topic": "塑料回收", "level": 1, "education": 3, "genre": "说明文",
     "opening": "Recycling plastic is harder than putting a bottle into the right "
                "bin.",
     "closing": "That is why the bottle in your hand is the easy case.",
     "points": ["瓶子只含一种塑料，好处理", "包装常混几种材料，要先分开",
                "很多城市只回收瓶子"]},
    {"topic": "蜜蜂的舞蹈", "level": 1, "education": 3, "genre": "科普说明文",
     "opening": "Honeybees tell each other where food is by dancing.",
     "closing": "A single dance carries both direction and distance.",
     "points": ["在蜂巢表面跳八字形", "直线段相对重力的方向指向花丛",
                "舞蹈的长短表示距离"]},
    {"topic": "火星之旅", "level": 0, "education": 5, "genre": "说明文",
     "opening": "Sending a person to Mars raises problems that no previous mission "
                "has solved.",
     "closing": "The hardest part may not be the technology.",
     "points": ["单程约七个月", "补给只能自带或在舱内生产",
                "乘员要长期与所有人隔绝"]},
    {"topic": "方言的价值", "level": 1, "education": 3, "genre": "议论文",
     "opening": "A dialect is not an incorrect version of a standard language.",
     "closing": "When a dialect disappears, something else goes with it.",
     "points": ["有自己的语法和词汇系统", "方言消失会带走一些知识",
                "有些渔村对几十种风各有名字"]},
    {"topic": "屏幕阅读与纸书", "level": 1, "education": 3, "genre": "科普说明文",
     "opening": "Reading on a screen and reading on paper are not the same "
                "activity.",
     "closing": "The page itself, it seems, is part of the memory.",
     "points": ["纸书读者记住的细节更多", "纸上的位置可以当记忆线索",
                "滚动的屏幕给不了这个线索"]},
]

#: 看图作文：给方向，插图交给 ``image-gen`` 出。
BANK_PICTURE_BRIEFS = [
    {"topic": "清晨的图书馆", "level": 1, "genre": "看图作文",
     "scene": "一间靠窗的阅览室，晨光落在摊开的书上，桌边一杯还冒着热气的咖啡",
     "focus": "描写环境，写出一节课前的安静气氛",
     "points": ["用两三个句子写清楚画面", "写出画面里的一个人和他在做什么",
                "说明这个场景让你想到什么"]},
    {"topic": "旧书店的老板", "level": 0, "genre": "看图作文",
     "scene": "狭窄的旧书店，高到天花板的书架，柜台后一位戴着眼镜翻书的老人",
     "focus": "通过细节写人，不要直接下评语",
     "points": ["写柜台和书架的样子", "用动作或语言写这位老人",
                "用一句话收束你的印象"]},
    {"topic": "放学后的操场", "level": 1, "genre": "看图作文",
     "scene": "夕阳下的操场，跑道上有人在跑步，看台边几个学生坐着聊天，书包堆在脚边",
     "focus": "写出时间感和声音",
     "points": ["点明时间和地点", "写出你听到或看到的两三个细节",
                "写出这个时刻给你的感觉"]},
    {"topic": "雨中的车站", "level": 0, "genre": "看图作文",
     "scene": "雨天的公交站台，一把被风吹歪的伞，湿透的地面映着车灯，几个人在等车",
     "focus": "记叙一件小事，不要写成说明文",
     "points": ["交代天气和场所", "写一个发生在站台上的具体瞬间",
                "写出这件事的结尾"]},
    {"topic": "厨房里的第一次", "level": 1, "genre": "看图作文",
     "scene": "凌乱的厨房，砧板上切了一半的菜，锅里的水正要溢出，一个学生手忙脚乱地关火",
     "focus": "以第一人称写，写出动作和心情",
     "points": ["描写厨房的样子", "写清楚你做错了什么、怎么补救",
                "写出这一次学到的东西"]},
]

#: 翻译练习。「待译原文」是**教师选的**（短句，库里有）；学生译文由学生自己写。
BANK_TRANS_SENTENCES = [
    {"src_lang": "en", "tgt_lang": "zh", "focus": "时态", "level": 1,
     "src_text": "By the time we arrived at the station, the train had already left."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "被动语态", "level": 1,
     "src_text": "The bridge was rebuilt last year using materials from the old one."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "定语从句", "level": 0,
     "src_text": "She recommended a book that changed the way I think about work."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "介词", "level": 1,
     "src_text": "The meeting has been put off until next Thursday."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "长句拆分", "level": 0,
     "src_text": "Although the experiment failed twice, the results still gave the "
                 "team useful information about what to avoid."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "虚拟语气", "level": 0,
     "src_text": "If the weather had been better, we would have finished the survey "
                 "a week earlier."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "量词", "level": 1,
     "src_text": "A small piece of advice from a stranger changed my whole plan."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "情态动词", "level": 1,
     "src_text": "You needn't have brought anything to the dinner."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "名词性从句", "level": 0,
     "src_text": "What surprised me most was that nobody had noticed the error."},
    {"src_lang": "en", "tgt_lang": "zh", "focus": "分词结构", "level": 0,
     "src_text": "Having lived in the city for ten years, she still preferred the "
                 "quiet of the countryside."},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "主谓一致", "level": 1,
     "src_text": "我们班每个学生都参加了这次志愿活动。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "成语", "level": 0,
     "src_text": "他做事一向三思而后行。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "无主句", "level": 1,
     "src_text": "据报道，这项政策将于明年开始实施。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "把字句", "level": 1,
     "src_text": "请把这封信转交给你的班主任。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "时间状语", "level": 0,
     "src_text": "随着人工智能的发展，翻译工作正在发生巨大的变化。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "并列结构", "level": 1,
     "src_text": "他不仅会弹钢琴，而且擅长画画。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "条件句", "level": 1,
     "src_text": "如果明天不下雨，我们就去爬山。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "插入语", "level": 0,
     "src_text": "这个方案，据我所知，还没有经过测试。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "比较结构", "level": 1,
     "src_text": "这本书比我想象的有趣得多。"},
    {"src_lang": "zh", "tgt_lang": "en", "focus": "让步状语", "level": 0,
     "src_text": "尽管困难重重，项目还是按期完成了。"},
]

#: 听力材料脚本。**只给要点，脚本交给 ``text-gen`` 起草**，
#: 音频交给 ``speech`` 合成。
BANK_LISTENING_BRIEFS = [
    {"topic": "图书馆闭馆通知", "level": 1, "education": 3, "genre": "通知",
     "opening": "Good afternoon, everyone. I have an announcement about the "
                "library.",
     "closing": "Thank you for your attention, and enjoy the rest of your day.",
     "points": ["本周六图书馆闭馆一天", "闭馆原因是要更换照明系统",
                "还书可以投门口的还书箱"]},
    {"topic": "新学期社团招新", "level": 1, "education": 3, "genre": "讲话",
     "opening": "Welcome back, everyone. Let me tell you about the clubs this "
                "term.",
     "closing": "Sign up before Friday if you are interested.",
     "points": ["这学期新增一个摄影社", "报名截止到周五", "周三下午有一次体验活动"]},
    {"topic": "一场校际比赛", "level": 0, "education": 5, "genre": "通知",
     "opening": "Attention, please. Here is the result of last Saturday's match.",
     "closing": "Well done, and thank you to everyone who came.",
     "points": ["比分是二比一", "决胜球在最后五分钟", "下一场是下周六下午三点"]},
    {"topic": "天气与出行提示", "level": 1, "education": 3, "genre": "播报",
     "opening": "Here is the travel information for this weekend.",
     "closing": "Please plan your journey in advance.",
     "points": ["周六有雨，建议提前出门", "地铁二号线周末检修", "校车照常运行"]},
    {"topic": "食堂的新安排", "level": 1, "education": 3, "genre": "通知",
     "opening": "Before you leave, I need to tell you about a change in the "
                "dining hall.",
     "closing": "We hope this makes lunchtime easier for everyone.",
     "points": ["午餐时间提前到十一点半", "新增一个取餐窗口",
                "周一至周五供应素食套餐"]},
    {"topic": "一次夏令营说明", "level": 0, "education": 5, "genre": "讲话",
     "opening": "Good morning. This meeting is for students interested in the "
                "summer camp.",
     "closing": "That is all. Welcome aboard.",
     "points": ["营地在一座山脚下的中学", "为期十天，两人一间",
                "需要自己带睡袋"]},
    {"topic": "博物馆参观须知", "level": 0, "education": 5, "genre": "须知",
     "opening": "Welcome to the city museum. Before you go in, a few things to "
                "remember.",
     "closing": "The guards will be happy to help if you have any questions.",
     "points": ["大厅可以拍照，展厅内不可以", "寄存处在入口右侧",
                "每个整点有一场免费讲解"]},
    {"topic": "英语角的安排", "level": 1, "education": 3, "genre": "通知",
     "opening": "Hello everyone, and thank you for coming to the first English "
                "Corner of the term.",
     "closing": "See you next Thursday.",
     "points": ["每周四下午四点在阅览室", "本期主题是旅行",
                "每次限二十人，需要提前报名"]},
]

BANKS = {
    "passage_en": BANK_PASSAGE_EN,
    "essay_prompts": BANK_ESSAY_PROMPTS,
    "reading_briefs": BANK_READING_BRIEFS,
    "picture_briefs": BANK_PICTURE_BRIEFS,
    "trans_sentences": BANK_TRANS_SENTENCES,
    "listening_briefs": BANK_LISTENING_BRIEFS,
}

# 音色白名单（跟 speech.SpeechAPI.SPEAKERS 一致，但**不 import** 那个模块——
# 这里只要名字，不需要它连带 import requests / socketio）。
SPEAKERS_EN = ("en_luka", "us_annie")

_LANG_NAME = {"en": "英语", "zh": "中文"}

_PLACEHOLDER = re.compile(r"\{\{([^{}]+)\}\}")


# ======================================================================
# 任务定义
# ======================================================================
#
# 一条任务 = 一个**执行应用** + 若干**素材**。素材可以：
#   * 交给某个应用生成（`app="speech"` 之类）
#   * 交给学生（`app="student"`）
#   * 交给教师（`app="teacher"`，通常库里已经给了）
#
# 步骤表里的 `args` 支持 `{{slot:名字}}` 占位符，在**生成期**解析成最终值，
# 所以任务清单里看到的是可以直接拿去执行的东西。

def _tts_demo(text_slot="text"):
    return {
        "label": "朗读示范音频", "app": "speech", "who": "平台（TTS 合成）",
        "purpose": "给学生一段标准范读，也作为朗读原文的依据",
        "command": "speech say",
        "args": {"text": "{{slot:%s}}" % text_slot,
                 "speaker": "{{slot:speaker}}", "language": 2,
                 "speed": "{{slot:speed}}"},
        "saves_as": "materials/{{task:no}}-demo.mp3",
        "path_slot": "demo_path",
        "output": "一个 mp3 音频地址 / 文件",
        "handoff": "把这段文字念成音频，音色 {{slot:speaker}}，语速 {{slot:speed}}",
    }


def _student_recording():
    return {
        "label": "学生朗读录音", "app": "student", "who": "学生",
        "purpose": "这就是要被评阅的那段音频",
        "saves_as": "materials/{{task:no}}-recording.mp3",
        "output": "学生录的一段音频",
        "handoff": "把学生录的音频存好，下一步交给口语评阅",
    }


def _student_writing(slot, label, note):
    return {
        "label": label, "app": "student", "who": "学生",
        "purpose": note,
        "saves_as": "materials/{{task:no}}-%s.txt" % slot,
        "output": "学生写的正文",
        "handoff": "把学生写的正文存好，下一步交给评阅",
    }


TASKS = {
    # ------------------------------------------------------------------
    "oral-drill": {
        "title": "英语朗读评测：{title}",
        "scenario": "口语练习",
        "goal": "学生朗读一段短文、录音提交，得到总分和逐词发音反馈",
        "bank": "passage_en",
        "keys": ["title"],
        "student_slot": "recording",
        "slots": {},
        "tags": ["口语", "朗读", "发音", "口语评阅"],
        "params": {"speaker": list(SPEAKERS_EN), "speed": [0.8, 1.0, 1.2]},
        "audience": "中学或大学英语课堂，一人一段，约 1 分钟",
        "brief": "朗读下面这篇短文，录音后提交。系统会给出总分和逐词发音建议。"
                 "\n\n短文：{{slot:text}}",
        "materials": [_tts_demo(), _student_recording()],
        "steps": [{
            "label": "口语评阅", "app": "oral-review",
            "purpose": "给这段朗读打分并给出发音反馈",
            "command": "oral review", "m_from": "recording",
            "shell": 'oral review {{m}} \\\n'
                     '    --content "{{slot:text}}" --ques-type 1', 
            "output": "百分制总分 + 逐词发音建议",
            "handoff": "评阅这段朗读录音，朗读原文是「{{slot:text}}」",
        }],
        "notes": [
            "示范音频是 TTS 合成的**标准发音**，拿它去评阅分数基本都偏高"
            "（实测 95–98）。所以这条任务的价值在「给学生一段范读」和"
            "「看反馈里该怎么读」，**不在测出学生多差**。",
            "学生的录音要单独存成文件，评阅走 `oral review <录音> --content …`。",
        ],
    },

    # ------------------------------------------------------------------
    "essay-writing": {
        "title": "英语写作：{topic}",
        "scenario": "写作练习",
        "goal": "学生按题面写一篇英语作文，得到总分、分项和逐句纠错",
        "bank": "essay_prompts",
        "keys": ["topic"],
        "student_slot": "submission",
        "slots": {},
        "tags": ["写作", "作文", "作文评阅"],
        "params": {},
        "audience": "中学或大学英语课堂，课后写作，约 30 分钟",
        "brief": "按下面的题面写一篇英语作文。\n\n"
                 "体裁：{{slot:genre}}　词数：{{slot:words}}　"
                 "学段：{{slot:levelName}}\n\n"
                 "{{slot:prompt}}\n\n要点提示：{{slot:hints}}",
        "materials": [
            {"label": "作文题面", "app": "teacher", "who": "教师",
             "purpose": "题面和要点——真实作文题本来就是这样给的",
             "saves_as": "（题面已写在任务里）",
             "output": "题面 + 体裁 + 词数 + 要点提示",
             "handoff": "把题面发给学生"},
            _student_writing("submission", "学生作文正文",
                             "学生按题面写的正文，就是要被评阅的那篇"),
        ],
        "steps": [{
            "label": "作文评阅", "app": "review",
            "purpose": "给这篇作文打分，并给出逐句纠错与改写建议",
            "command": "review essay", "m_from": "submission",
            "shell": "review essay --path {{m}} \\\n"
                     "    --topic \"{{slot:topic}}\" --level {{slot:level}}",
            "output": "加权总分 + 四个分项 + 逐句纠错",
            "handoff": "评阅这篇作文，题目是「{{slot:topic}}」，学段 {{slot:levelName}}",
        }],
        "notes": [
            "**别拿平台生成的文章去评阅**：那只会拿高分，学生学不到东西。"
            "正文必须是学生自己写的。",
            "评阅结果里 `content` / `language` / `organization` / `mechanics` "
            "是**评语字符串**，不是分数；分数只有 `*Score` 后缀的字段。",
        ],
    },

    # ------------------------------------------------------------------
    "reading-comprehension": {
        "title": "阅读理解练习：{topic}",
        "scenario": "阅读理解",
        "goal": "先起草一篇阅读材料，再据此出题，学生作答",
        "bank": "reading_briefs",
        "keys": ["topic"],
        "student_slot": "answers",
        "slots": {},
        "tags": ["阅读", "出题", "阅读理解"],
        "params": {"ploy": [f"{c}:1" for c in sorted(PLOY_CODES)]},
        "audience": "中学或大学英语课堂，一节阅读课",
        "brief": "读下面这篇材料，然后回答系统给出的题目。\n\n"
                 "（阅读材料：{{slot:passage_path}}）\n\n"
                 "主题：{{slot:topic}}　体裁：{{slot:genre}}",
        "materials": [
            {"label": "阅读材料正文", "app": "text-gen", "who": "平台（文本生成）",
             "purpose": "按下面的要点起草一篇 {{slot:genre}}，作为出题的素材",
             "command": "article create → article continue",
             "shell": "article create --title \"{{slot:topic}}\"     # → articleId\n"
                      "article continue <articleId> \\\n"
                      "    --start \"{{slot:opening}}\" \\\n"
                      "    --end \"{{slot:closing}}\" > {{m}}",
             "extra": "要点（写的时候要覆盖到）：{{slot:points}}",
             "saves_as": "materials/{{task:no}}-passage.txt",
             "path_slot": "passage_path",
             "output": "一篇 150–250 词的阅读材料",
             "handoff": "按要点写一篇 {{slot:genre}}：{{slot:points}}"},
            _student_writing("answers", "学生作答",
                             "学生读完材料后的答案（书面）"),
        ],
        "steps": [{
            "label": "智能出题", "app": "question-gen",
            "purpose": "拿这篇材料建一份题库，并生成一组题",
            "command": "questions create-material → questions generate",
            "m_from": "passage_path",
            "shell": "questions create-material --path {{m}} "
                     "--education {{slot:education}}   # → rmId\n"
                     "questions generate <rmId> --ploy {{slot:ploy}}",
            "output": "rmId + 一组带 quesId 的题目",
            "handoff": "就 materials/{{task:no}}-passage.txt 这段材料出一组题"
                       "（{{slot:ployName}}），学段 {{slot:educationName}}",
        }],
        "notes": [
            "**阅读材料删不掉**：平台没有 `rm/delete`，建一条少一条。"
            "所以这条任务适合按学期规划好条数再跑。",
            "平台**没有可用的答题接口**（文档里的 `ques/ans` 实测 404），"
            "学生作答只能人工收。",
            "`create-material` 的 `subType` 和出题策略的 `code` **不是一套编号**，"
            "别互相套用。",
        ],
    },

    # ------------------------------------------------------------------
    "translation-drill": {
        "title": "翻译练习（{src_langName} → {tgt_langName}）：{focus}",
        "scenario": "翻译练习",
        "goal": "学生按原文翻译，得到评分和说明",
        "bank": "trans_sentences",
        "keys": ["src_text"],
        "student_slot": "submission",
        "slots": {},
        "tags": ["翻译", "翻译评阅", "英汉互译"],
        "params": {},
        "audience": "中学或大学英语课堂，随堂练习",
        "brief": "把下面的{{slot:src_langName}}句子译成{{slot:tgt_langName}}。\n\n"
                 "原文：{{slot:src_text}}\n考点：{{slot:focus}}",
        "materials": [
            {"label": "待译原文", "app": "teacher", "who": "教师",
             "purpose": "挑好的句子，考点是 {{slot:focus}}",
             "saves_as": "（原文已写在任务里）",
             "output": "一句待译原文",
             "handoff": "把原文发给学生"},
            _student_writing("submission", "学生译文",
                             "学生翻的那一份，就是要被评阅的那份"),
        ],
        "steps": [{
            "label": "翻译评阅", "app": "trans-review",
            "purpose": "给这份译文打分（**只打分，不改译文**）",
            "command": "trans review", "m_from": "submission",
            "shell": 'tr review \\\n'
                     '    --src-text "{{slot:src_text}}" --tgt-file {{m}} \\\n'
                     '    --src-lang {{slot:src_lang}} --tgt-lang {{slot:tgt_lang}}', 
            "output": "一个百分制分数",
            "handoff": "给这份译文打分：原文「{{slot:src_text}}」，"
                       "学生译文见 materials/{{task:no}}-submission.txt",
        }],
        "notes": [
            "**翻译评阅只给分，不产出译文。** 想要「改后的译文」这个接口给不了。",
            "语种码只认小写 `en` / `zh`，**写错不报错、只给假分数**——"
            "命令行里已经用 choices 挡住了。",
            "要评另一对语种必须**新建记录**，不要复用同一条 wmId。",
        ],
    },

    # ------------------------------------------------------------------
    "picture-writing": {
        "title": "看图作文：{topic}",
        "scenario": "写作练习",
        "goal": "先出一张插图，学生看图写作，再交评阅",
        "bank": "picture_briefs",
        "keys": ["topic"],
        "student_slot": "submission",
        "slots": {},
        "tags": ["写作", "看图作文", "配图", "作文评阅"],
        "params": {},
        "audience": "中学英语写作课，一课时",
        "brief": "看这张图写一篇短文。\n\n"
                 "（插图：{{slot:image_path}}）\n\n"
                 "题目：{{slot:topic}}　体裁：{{slot:genre}}\n"
                 "要求：{{slot:focus}}\n\n要点提示：{{slot:points}}",
        "materials": [
            {"label": "插图", "app": "image-gen", "who": "平台（AI 绘画）",
             "purpose": "出这张图，学生要看着它写",
             "command": "image draw",
             "shell": 'image draw "{{slot:scene}}" '
                      '--style general_v2.1_L --size 正方形', 
             "saves_as": "materials/{{task:no}}-picture.png",
             "path_slot": "image_path",
             "output": "一张图片地址",
             "handoff": "画一张图：{{slot:scene}}"},
            _student_writing("submission", "学生作文正文",
                             "学生看图写的那篇，就是要被评阅的那篇"),
        ],
        "steps": [{
            "label": "作文评阅", "app": "review",
            "purpose": "给这篇看图作文打分 + 逐句纠错",
            "command": "review essay", "m_from": "submission",
            "shell": "review essay --path {{m}} \\\n"
                     "    --topic \"{{slot:topic}}\" --level {{slot:level}}",
            "output": "加权总分 + 四个分项 + 逐句纠错",
            "handoff": "评阅这篇看图作文，题目是「{{slot:topic}}」，"
                       "学段 {{slot:levelName}}",
        }],
        "notes": [
            "**出图必须用 `general_v2.1_L`**：平台接口文档点名的那些风格"
            "（`manhua` / `shuicai` / `xieshi` 之类）虽然也在线上白名单里，"
            "但提交后会被静默改写、然后失败或挂住。尺寸也要跟着风格走。",
            "出图不是秒级，`--wait` 留足；超时是退出码 3，**不是失败**。",
        ],
    },

    # ------------------------------------------------------------------
    "listening-comprehension": {
        "title": "听力练习：{topic}",
        "scenario": "听力训练",
        "goal": "先起草一段听力脚本、合成音频，再据此出题",
        "bank": "listening_briefs",
        "keys": ["topic"],
        "student_slot": "answers",
        "slots": {},
        "tags": ["听力", "出题", "TTS"],
        "params": {"ploy": [f"{c}:1" for c in sorted(PLOY_CODES)],
                   "speaker": list(SPEAKERS_EN), "speed": [0.8, 1.0]},
        "audience": "中学英语听力课，一节听说课",
        "brief": "先听音频，再回答系统给出的题目。\n\n"
                 "（音频：{{slot:audio_path}}）\n\n"
                 "主题：{{slot:topic}}　体裁：{{slot:genre}}",
        "materials": [
            {"label": "听力脚本", "app": "text-gen", "who": "平台（文本生成）",
             "purpose": "按要点起草一段 {{slot:genre}} 的英文听力脚本",
             "command": "article create → article continue",
             "path_slot": "script_path",
             "shell": "article create --title \"{{slot:topic}}\"     # → articleId\n"
                      "article continue <articleId> \\\n"
                      "    --start \"{{slot:opening}}\" \\\n"
                      "    --end \"{{slot:closing}}\" > {{m}}",
             "extra": "要点（写的时候要覆盖到）：{{slot:points}}",
             "saves_as": "materials/{{task:no}}-script.txt",
             "path_slot": "script_path",
             "output": "一段 120–200 词的听力脚本",
             "handoff": "按要点写一段英文{{slot:genre}}：{{slot:points}}"},
            {"label": "听力音频", "app": "speech", "who": "平台（TTS 合成）",
             "purpose": "把脚本念成音频，学生听这个",
             "command": "speech say",
             "shell": "speech say \"$(cat materials/{{task:no}}-script.txt)\" "
                      "--speaker {{slot:speaker}} --language 2 "
                      "--speed {{slot:speed}} --out {{m}}",
             "saves_as": "materials/{{task:no}}-audio.mp3",
             "path_slot": "audio_path",
             "output": "一个 mp3 音频地址 / 文件",
             "handoff": "把这段脚本念成音频，音色 {{slot:speaker}}，"
                       "语速 {{slot:speed}}"},
            _student_writing("answers", "学生作答",
                             "学生听完音频后的答案"),
        ],
        "steps": [{
            "label": "智能出题", "app": "question-gen",
            "purpose": "据听力脚本出一组题",
            "command": "questions create-material → questions generate",
            "m_from": "script_path",
            "shell": "questions create-material --path {{m}} "
                     "--education {{slot:education}}   # → rmId\n"
                     "questions generate <rmId> --ploy {{slot:ploy}}",
            "output": "rmId + 一组带 quesId 的题目",
            "handoff": "就 materials/{{task:no}}-script.txt 这段听力材料出一组题"
                       "（{{slot:ployName}}），学段 {{slot:educationName}}",
        }],
        "notes": [
            "**听力脚本要先写出来再合成**——`speech say` 直接吃文本，"
            "所以脚本是它的输入。脚本本身也是听力原文，留给教师核对。",
            "出题那一步同样受「**阅读材料删不掉**」约束（平台的 `rm` 没有删除接口）。",
            "TTS 用的音色和语速会影响难度：慢速（0.8）适合初中，原速适合高中以上。",
        ],
    },
}

ALL_TASKS = list(TASKS)

#: ``constants.Level`` 的名字 → ``question_gen.EDUCATION`` 的编号。
#: 两套编号**互不相通**（EDUCATION 没有 0），只能按名字对。
_EDUCATION_BY_NAME = {"小学": 1, "初中": 2, "高中": 3, "大学": 5}
_EDUCATION_NAME = {v: k for k, v in _EDUCATION_BY_NAME.items()}
_EDUCATION_NAME[4] = "职教"
_EDUCATION_NAME[6] = "研究生"
_EDUCATION_NAME[7] = "其他"


# ======================================================================
# 生成
# ======================================================================
def _fill_row(task_name, chain, row):
    """把素材行铺成完整的 ``slots``（内容 + 派生名）。

    ``row`` 是 ``BANKS`` 里的一行——**浅拷贝**，所以下面凡是动过的地方一律
    **重新绑定**（``slots[x] = …``），不要 ``.append`` / ``.update`` 到行内
    已有的 list/dict 上，否则会改到 ``BANKS`` 那个常量本身（踩过）。
    """
    slots = dict(row)
    slots["levelName"] = Level.NAME.get(slots.get("level"), "")
    slots["levelCode"] = slots.get("level", Level.COLLEGE)
    if "src_lang" in slots:
        slots["src_langName"] = _LANG_NAME.get(slots["src_lang"], slots["src_lang"])
        slots["tgt_langName"] = _LANG_NAME.get(slots["tgt_lang"], slots["tgt_lang"])
    if "hints" in slots:
        slots["hints"] = "　".join(
            f"{i}. {h}" for i, h in enumerate(slots.get("hints") or [], 1))
    if "points" in slots:
        slots["points"] = "；".join(slots.get("points") or [])
    if "levelName" in slots:
        slots["education"] = _EDUCATION_BY_NAME.get(slots["levelName"], 5)
        slots["educationName"] = _EDUCATION_NAME.get(slots["education"], "")
    return slots


def _resolve(value, ctx):
    """把 ``{{slot:…}}`` / ``{{task:…}}`` 解析成最终值。

    整个字符串就是一个占位符时**原样返回那个值**（保住类型）——
    `"{{slot:speed}}"` 要还原成浮点 `1.0`，不是字符串。
    """
    if isinstance(value, dict):
        return {k: _resolve(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    whole = _PLACEHOLDER.fullmatch(value)
    if whole:
        return _lookup(whole.group(1), ctx)

    out, pos = [], 0
    for m in _PLACEHOLDER.finditer(value):
        out.append(value[pos:m.start()])
        got = _lookup(m.group(1), ctx)
        out.append("" if got is None else str(got))
        pos = m.end()
    out.append(value[pos:])
    return "".join(out)


def _lookup(expr, ctx):
    head, _, rest = expr.strip().partition(":")
    if head == "m":                    # `{{m}}`：本条目自己的文件路径
        return ctx.get("m", "")
    head, rest = head.strip(), rest.strip()
    if head == "slot":
        if rest not in ctx["slots"]:
            raise LocalCheckError(f"占位符 {{{{slot:{rest}}}}} 引用了不存在的槽位")
        return ctx["slots"][rest]
    if head == "task":
        if rest not in ctx["task"]:
            raise LocalCheckError(f"占位符 {{{{task:{rest}}}}} 不认识")
        return ctx["task"][rest]
    if head == "set":
        if rest not in ctx["set"]:
            raise LocalCheckError(f"占位符 {{{{set:{rest}}}}} 不认识")
        return ctx["set"][rest]
    raise LocalCheckError(f"不认识的占位符 {{{{ {expr} }}}}（只支持 slot: / task: / set:）")


def _handoff(tpl, ctx):
    """交接语——给 guide 看的那句话，占位符也解析掉。"""
    return _resolve(tpl, ctx) if tpl else ""


def _pick(rng, choices, i):
    """参数维度打散：**带上位置偏移**，避免同一批里参数全挤在一起。"""
    if not choices:
        return None
    return choices[(rng.randrange(len(choices)) + i) % len(choices)]


def _build(task_name, chain, task_no, set_id, slots):
    """把一条任务铺成任务卡。

    .. note::
       **素材先算，执行后算。** 学生交上来的东西（录音、作文正文、译文、作答）
       存到哪个文件，执行那一步要引用（`@materials/03-submission.txt`）。
       所以第一遍先把素材的路径写进 ``slots``，第二遍才渲染执行步骤——
       否则执行步骤里会出现"引用了不存在的槽位"。
    """
    ctx = {"slots": slots, "task": {"no": f"{task_no:02d}"}, "set": {"id": set_id}}

    def render(spec):
        # `{{m}}` 是"这条素材自己存到的那个文件"的简写——写 shell 时省得
        # 把 `materials/03-passage.txt` 再抄一遍。
        local = dict(ctx)
        local["m"] = _resolve(spec.get("saves_as", ""), ctx) \
            if spec.get("saves_as") else ""
        item = {
            "label": _resolve(spec["label"], ctx),
            "who": _resolve(spec["who"], ctx),
            "purpose": _resolve(spec.get("purpose", ""), ctx),
            "saves_as": _resolve(spec.get("saves_as", ""), ctx),
            "output": _resolve(spec.get("output", ""), ctx),
            "handoff": _handoff(spec.get("handoff"), ctx),
        }
        for key in ("app", "command"):
            if spec.get(key):
                item[key] = spec[key]
        if spec.get("args"):
            item["args"] = {k: _resolve(v, ctx) for k, v in spec["args"].items()}
        if spec.get("shell"):
            item["shell"] = _resolve(spec["shell"], local)
        if spec.get("extra"):
            item["extra"] = _resolve(spec["extra"], ctx)
        return item

    # 第一遍：只算路径。学生交上来的东西存到哪、素材产出存到哪，
    # 执行那一步和题干都要引用，所以先把它们写进 slots。
    for spec in chain["materials"]:
        path = _resolve(spec.get("saves_as", ""), ctx)
        if spec.get("path_slot"):
            slots[spec["path_slot"]] = path
        kind = chain.get("student_slot")
        if spec.get("app") == "student" and kind:
            slots[kind] = path

    materials = [render(spec) for spec in chain["materials"]]

    steps = []
    for i, spec in enumerate(chain["steps"], 1):
        step_ctx = dict(ctx)
        # 执行那一步吃哪个文件，由步骤自己声明（`m_from`）。
        # 早期版本按 `student_slot` 猜，结果出题那一步指向了学生的作答
        # 而不是阅读材料本身——**看着对、其实是错的**。
        kind = spec.get("m_from") or chain.get("student_slot")
        step_ctx["m"] = slots.get(kind, "") if kind else ""
        steps.append({
            "n": i,
            "label": _resolve(spec["label"], ctx),
            "application": spec["app"],
            "purpose": _resolve(spec.get("purpose", ""), ctx),
            "command": spec.get("command", ""),
            "args": {k: _resolve(v, ctx) for k, v in (spec.get("args") or {}).items()},
            "shell": _resolve(spec["shell"], step_ctx) if spec.get("shell") else "",
            "output": _resolve(spec.get("output", ""), ctx),
            "handoff": _handoff(spec.get("handoff"), ctx),
        })

    title = chain["title"].format(
        **{k: (v if v is not None else "") for k, v in slots.items()})
    return {
        "taskNo": f"{task_no:02d}",
        "setId": set_id,
        "taskKey": task_name,
        "title": title,
        "application": chain["steps"][0]["app"],
        "scenario": chain["scenario"],
        "goal": _resolve(chain["goal"], ctx),
        "audience": _resolve(chain["audience"], ctx),
        "level": slots.get("level", Level.COLLEGE),
        "levelName": slots.get("levelName", ""),
        "tags": list(chain["tags"]),
        "brief": _resolve(chain["brief"], ctx),
        "materials": materials,
        "steps": steps,
        "notes": [_resolve(n, ctx) for n in chain.get("notes", [])],
    }


def gen_batch(task_names, count, *, seed=None, speaker=None):
    """生成一套任务。返回 ``(set_id, tasks, manifest)``。**零平台调用。**

    :param task_names: 任务名列表；``"all"`` 表示全部
    :param count: **每种任务**各几条
    :param seed: 随机种子；给了就完全可复现
    :param speaker: 如果给了，就把所有任务里的音色固定成它（用户指定时用）
    """
    if task_names in (None, "all", ["all"]):
        names = list(TASKS)
    else:
        names = list(task_names)
    for name in names:
        if name not in TASKS:
            raise LocalCheckError(f"没有这种任务：{name!r}。可选："
                                  f"{', '.join(ALL_TASKS)}，或 all")
    if count < 1:
        raise LocalCheckError("--count 至少是 1")
    if speaker is not None and speaker not in SPEAKERS_EN:
        raise LocalCheckError(f"音色 {speaker!r} 不在白名单里："
                              f"{', '.join(SPEAKERS_EN)}")

    rng = random.Random(seed)
    set_id = time.strftime("%Y%m%d-%H%M%S") + "-%04x" % rng.getrandbits(16)
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    tasks, used, no = [], set(), 0
    for name in names:
        chain = TASKS[name]
        pool = [_fill_row(name, chain, row) for row in BANKS[chain["bank"]]]
        rng.shuffle(pool)

        picked = []
        for slots in pool:
            sig = json.dumps({k: slots.get(k) for k in chain["keys"]},
                             sort_keys=True, ensure_ascii=False)
            if sig in used:
                continue
            used.add(sig)
            picked.append(slots)
            if len(picked) == count:
                break
        if len(picked) < count:
            raise LocalCheckError(
                f"「{chain['title'].split('：')[0]}」的素材只有 {len(pool)} 组，"
                f"凑不出 {count} 条内容不重复的任务——请减少 --count，"
                f"或给素材库补内容")

        for i, slots in enumerate(picked, 1):
            full = dict(slots)          # **新建的 dict**，绝不共享可变对象
            for key, choices in chain["params"].items():
                full[key] = speaker if (key == "speaker" and speaker) \
                    else _pick(rng, choices, i)
            if full.get("ploy"):
                code = str(full["ploy"]).split(":")[0]
                full["ployName"] = PLOY_CODES.get(int(code), "")
            no += 1
            tasks.append(_build(name, chain, no, set_id, full))

    manifest = {
        "setId": set_id,
        "generatedAt": generated_at,
        "seed": seed,
        "speaker": speaker,
        "count": len(tasks),
        "byTask": {n: sum(1 for t in tasks if t["taskKey"] == n) for n in names},
        "distinct": len({(t["taskKey"], t["title"]) for t in tasks}),
        "applications": sorted({t["application"] for t in tasks}),
        "tasks": [{"taskNo": t["taskNo"], "title": t["title"],
                   "taskKey": t["taskKey"], "application": t["application"]}
                  for t in tasks],
    }
    if manifest["distinct"] != len(tasks):
        # 自证字段：任务名 + 标题必须两两不同，否则上面早该抛错
        raise LocalCheckError("内部不一致：标题有重复——这不该发生，请报告")
    return set_id, tasks, manifest


# ======================================================================
# 落盘
# ======================================================================
def root_dir(out=None):
    """任务集的根目录。默认 ``~/.cache/unipus-aigc/tasks``。"""
    if out:
        return os.path.abspath(os.path.expanduser(out))
    base = config.cache_dir()
    if not base:
        raise AigcError("主目录不可用，无法确定落盘位置——请显式给 --out")
    return os.path.join(base, "tasks")


def write_set(set_id, tasks, manifest, *, out=None):
    """写 ``manifest.json`` + ``清单.md`` + ``tasks/NN-*.md|.json``。返回目录。"""
    dest = os.path.join(root_dir(out), set_id)
    tdir = os.path.join(dest, "tasks")
    mdir = os.path.join(dest, "materials")
    os.makedirs(tdir, exist_ok=True)
    os.makedirs(mdir, exist_ok=True)
    _write_json(os.path.join(dest, "manifest.json"), manifest)
    with open(os.path.join(dest, "清单.md"), "w", encoding="utf-8") as fh:
        fh.write(render_index(tasks, manifest))
    for task in tasks:
        stem = f"{task['taskNo']}-{task['taskKey']}"
        _write_json(os.path.join(tdir, stem + ".json"), task)
        with open(os.path.join(tdir, stem + ".md"), "w", encoding="utf-8") as fh:
            fh.write(render_markdown(task))
    return dest


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def sets(out=None):
    """已有的任务集目录（新→旧）。"""
    root = root_dir(out)
    if not os.path.isdir(root):
        return []
    return sorted((n for n in os.listdir(root)
                   if os.path.isfile(os.path.join(root, n, "manifest.json"))),
                  reverse=True)


def load_set(set_id, *, out=None):
    path = os.path.join(root_dir(out), set_id, "manifest.json")
    if not os.path.isfile(path):
        raise AigcError(f"找不到任务集 {set_id}（{path}）")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def set_tasks(set_id, *, out=None):
    """一个任务集里的全部任务（按编号）。"""
    tdir = os.path.join(root_dir(out), set_id, "tasks")
    if not os.path.isdir(tdir):
        raise AigcError(f"任务集 {set_id} 里没有任务（{tdir}）")
    out_list = []
    for name in sorted(os.listdir(tdir)):
        if name.endswith(".json"):
            with open(os.path.join(tdir, name), encoding="utf-8") as fh:
                out_list.append(json.load(fh))
    return out_list


def find_task(no_or_title, *, out=None, newest_only=False):
    """按**编号**（``03``）或标题片段找一条任务。

    :param newest_only: 只看**最新那套**清单。`tasks show 03` / `tasks handoff 03`
        默认这样——编号在每套清单里都是从 01 开始，跨套找必然歧义。
        要跨套找就显式给标题片段。
    """
    root = root_dir(out)
    if not os.path.isdir(root):
        raise AigcError(f"还没有任何任务集（{root}）——先跑 `tasks gen`")
    all_sets = sorted(os.listdir(root), reverse=True)
    if newest_only:
        all_sets = all_sets[:1]

    hits = []
    for set_id in all_sets:
        tdir = os.path.join(root, set_id, "tasks")
        if not os.path.isdir(tdir):
            continue
        for name in sorted(os.listdir(tdir)):
            if not name.endswith(".json"):
                continue
            with open(os.path.join(tdir, name), encoding="utf-8") as fh:
                task = json.load(fh)
            # 纯数字只当编号比；带别的字符才当标题/文件名片段
            if no_or_title.isdigit():
                matched = task["taskNo"] == no_or_title.zfill(2)
            else:
                matched = (no_or_title in task["title"]
                           or no_or_title in task["taskKey"]
                           or name[:-5].startswith(no_or_title))
            if matched:
                hits.append((set_id, os.path.join(tdir, name), task))
    if not hits:
        raise AigcError(f"找不到任务 {no_or_title}"
                        f"（在 {', '.join(all_sets)} 下扫过）")
    if len(hits) > 1:
        raise AigcError(f"{no_or_title} 匹配到 {len(hits)} 条任务——请说详细一点："
                        + "、".join(f"{s}/{t['taskNo']} {t['title']}"
                                   for s, _, t in hits[:5]))
    return hits[0]


# ======================================================================
# 渲染
# ======================================================================
def render_index(tasks, manifest):
    """``清单.md``——教师一眼能看完的目录。"""
    lines = [f"# 任务清单（{manifest['setId']}）", ""]
    lines.append(f"共 **{len(tasks)}** 条任务，"
                 f"覆盖 **{len(manifest['applications'])}** 个应用："
                 f"{'、'.join(manifest['applications'])}。")
    lines.append("")
    lines.append("> 这份清单由 `task-gen` 生成，**没有调用任何平台接口**。"
                 "每条任务都写明了交给哪个应用、素材从哪来。")
    lines.append("")
    lines.append("| # | 任务 | 交给哪个应用 | 场景 | 学段 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for task in tasks:
        lines.append(f"| {task['taskNo']} | {task['title']} | `{task['application']}` "
                     f"| {task['scenario']} | {task['levelName']} |")
    lines.append("")
    lines.append("## 按场景分组")
    lines.append("")
    by_scene = {}
    for task in tasks:
        by_scene.setdefault(task["scenario"], []).append(task)
    for scene, group in by_scene.items():
        lines.append(f"### {scene}（{len(group)} 条）")
        lines.append("")
        for task in group:
            lines.append(f"- **{task['taskNo']}　{task['title']}** —— "
                         f"{task['goal']}")
        lines.append("")
    lines.append("## 怎么用")
    lines.append("")
    lines.append("1. 每条任务的详细卡片在 `tasks/`，素材取件目录是 `materials/`。")
    lines.append("2. 任务卡里的**交接语**可以直接发给 `guide`，它会路由到对应的应用。")
    lines.append("3. 素材准备（比如合成示范音频、起草阅读材料）也是**任务**——"
                 "同样交给 `guide`，它会路由到 `speech` / `text-gen`。")
    lines.append("")
    return "\n".join(lines)


def render_markdown(task):
    """一条任务的卡片。"""
    lines = [f"# 任务 {task['taskNo']}　{task['title']}", ""]
    lines.append(f"- **交给哪个应用**：`{task['application']}`")
    lines.append(f"- **场景**：{task['scenario']}")
    lines.append(f"- **目标**：{task['goal']}")
    lines.append(f"- **学段**：{task['levelName'] or '—'}")
    lines.append(f"- **适用**：{task['audience']}")
    lines.append(f"- **标签**：{'、'.join(task['tags'])}")
    lines.append("")
    lines.append("## 题目")
    lines.append("")
    for ln in task["brief"].splitlines():
        lines.append(ln)
    lines.append("")
    lines.append("## 素材准备")
    lines.append("")
    lines.append("这条任务要用到下面的东西。**能交给平台生成的就交给平台生成**"
                 "（在「谁来做」里写明是哪个应用），学生自己做的不必。")
    lines.append("")
    lines.append("| # | 素材 | 谁来做 | 存到哪 |")
    lines.append("| --- | --- | --- | --- |")
    for i, mat in enumerate(task["materials"], 1):
        lines.append(f"| {i} | {mat['label']} | {mat['who']} "
                     f"| `{mat['saves_as']}` |")
    lines.append("")
    for i, mat in enumerate(task["materials"], 1):
        lines.append(f"### 素材 {i}　{mat['label']}")
        lines.append("")
        lines.append(f"- 谁来做：{mat['who']}")
        if mat.get("purpose"):
            lines.append(f"- 用途：{mat['purpose']}")
        if mat.get("command"):
            lines.append(f"- 命令：`{mat['command']}`")
        if mat.get("extra"):
            lines.append(f"- {mat['extra']}")
        if mat.get("shell"):
            lines.append("")
            lines.append("```bash")
            lines.append(mat["shell"])
            lines.append("```")
            lines.append("")
        lines.append(f"- 存到：`{mat['saves_as']}`")
        lines.append(f"- 产出：{mat['output']}")
        lines.append("")
        if mat.get("app") and mat["app"] not in ("student", "teacher"):
            lines.append("  交接给 `guide`：")
            lines.append("")
            lines.append(f"  > {mat['handoff']}")
            lines.append("")

    lines.append("## 交给 guide 执行")
    lines.append("")
    for step in task["steps"]:
        lines.append(f"**{step['label']}** → `{step['application']}`　"
                     f"命令：`{step['command']}`")
        lines.append("")
        lines.append(f"- 用途：{step['purpose']}")
        lines.append(f"- 产出：{step['output']}")
        lines.append("")
        if step.get("shell"):
            lines.append("```bash")
            lines.append(step["shell"])
            lines.append("```")
            lines.append("")
        lines.append("把下面这句话给 `guide`（它会路由到对应应用）：")
        lines.append("")
        lines.append(f"> {step['handoff']}")
        lines.append("")
    lines.append("## 注意")
    lines.append("")
    for note in task["notes"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def tasks_table():
    """``tasks list`` 用的总览（纯文本）。用**素材库第一行**渲染标题做样例。"""
    rows = []
    for name in ALL_TASKS:
        chain = TASKS[name]
        sample = _fill_row(name, chain, BANKS[chain["bank"]][0])
        rows.append(f"{name}　{chain['title'].format(**sample)}")
        rows.append(f"  做什么：{chain['goal']}")
        rows.append(f"  场景：{chain['scenario']}　交给：`{chain['steps'][0]['app']}`")
        mats = "、".join(f"{m['label']}（{m['who']}）" for m in chain["materials"])
        rows.append(f"  素材：{mats}")
        made_by = [m["app"] for m in chain["materials"]
                   if m.get("app") and m["app"] not in ("student", "teacher")]
        if made_by:
            rows.append(f"  素材由这些应用产出：{'、'.join(made_by)}")
        rows.append("")
    return "\n".join(rows).rstrip()
