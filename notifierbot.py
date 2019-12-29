import praw
from praw.models import Message, Comment
from praw.exceptions import APIException, NotFound
import multiprocessing
import redis
import os
import time
from urllib.parse import quote
import re

def get_posts(reddit, subreddit):
    print("parsing posts...")
    post_history = redis.from_url(os.environ.get('REDIS_URL'), db=0)
    for post in subreddit.stream.submissions(skip_existing=True):
        if post_history.exists(post.id) or "[tomt]" not in post.title.lower() or "[mod]" in post.title.lower() or "solved" in post.title.lower():
            print(f"skipped {post.id}")
            continue
        to = "notifier-bot"
        subject = post.id
        message = (f"Click 'send' to be notified if OP marks '{post.title}' as solved. You should receive a confirmation PM soon.\n\n"
                    "----------\n\n"
                    f"Make sure the message subject says '{post.id}' before sending.\n\n"
                    "Official reddit app users might need to type something in the message body before the send button is enabled.\n\n"
                    "This bot is still in beta, sorry if you come across any bugs. PM u/adityapstar if you have any questions/comments/complaints.")
        message = quote(message)
        link = f"https://www.reddit.com/message/compose/?to={to}&subject={subject}&message={message}"
        comment = (f"Click [here]({link}) if you'd like to be notified when this post is solved.\n\n"
                    f"^^Type ^^'{subject}' ^^in ^^the ^^message ^^subject ^^if ^^it ^^hasn't ^^already ^^been ^^filled ^^in. ")
        print(f"commenting in {post.title}")
        try:
            comment = post.reply(comment)
        except APIException:
            time.sleep(5)
            comment = post.reply(comment)
        post_history.set(post.id, comment.id)

def get_messages(reddit, inbox):
    print("parsing messages...")
    post_history = redis.from_url(os.environ.get("REDIS_URL"), db=0)
    subscriptions = redis.from_url(os.environ.get("REDIS_URL"), db=1)
    while True:
        for item in inbox.unread(limit=None):
            if isinstance(item, Comment):
                print("forwarding comment reply")
                reddit.redditor("adityapstar").message(f"comment reply from u/{item.author}", item.body+"\n\n"+item.context)
            elif isinstance(item, Message) and item.author and item.author != 'reddit':
                post_id = item.subject.replace("'", "").replace('"', "").replace(" ", "").lower()[:6]
                post = reddit.submission(id=post_id)
                if not post_history.exists(post_id):
                    message = ("Sorry, an error occurred while attempting to respond to your message. "
                               "If you want to subscribe to a post, make sure the message subject correctly contains the post ID of the post you want to subscribe to. "
                               "Click [here](https://www.reddit.com/user/notifier-bot/comments/cy1egg/unotifierbot_info/) for more information.")
                    try:
                        flair = post.link_flair_text
                        if flair and 'solved' in flair.lower():
                            message = ("Sorry, an error occurred while attempting to respond to your message. "
                                       f"It looks [that post](https://redd.it/{post_id}) was just marked solved. ")
                    except NotFound:
                        print("invalid message subject")
                    print("responding to invalid request")
                    if "re: " not in item.subject:
                        item.reply(message)
                    item.mark_read()
                    reddit.redditor('adityapstar').message('invalid req', item.subject+'\n'+item.body)
                    continue
                print(f"subscribing u/{item.author} to '{post.title}'")
                subscribers = [user.decode() for user in subscriptions.lrange(post_id, 0, subscriptions.llen(post_id)-1)]
                if (item.author.name in subscribers):
                    print(f"{item.author.name} is already subscribed to {post_id}")
                    item.mark_read()
                    item.reply(f"Sorry, it looks like you're already subscribed to '[{post.title}]({post.shortlink})'.")
                    continue
                num_subscribers = subscriptions.rpush(post_id, item.author.name)
                item.reply(f"You have successfully subscribed to '[{post.title}]({post.shortlink})'.")
                if post_history.exists(post_id):
                    comment_id = post_history.get(post_id).decode()
                    comment = reddit.comment(comment_id)
                    if num_subscribers == 1:
                        comment.edit(comment.body + " ^^1 ^^user ^^is ^^currently ^^subscribed ^^to ^^this ^^post. ")
                    else:
                        comment.edit(comment.body.split('. ')[0] + f". ^^{num_subscribers} ^^users ^^are ^^currently ^^subscribed ^^to ^^this ^^post. ")
            item.mark_read()

def get_comments(reddit, subreddit):
    print("parsing comments...")
    post_history = redis.from_url(os.environ.get('REDIS_URL'), db=0)
    subscriptions = redis.from_url(os.environ.get('REDIS_URL'), db=1)
    word = "solved"
    s = re.compile(r"\b%s" % word, re.I)
    for comment in subreddit.stream.comments():
        if s.search(comment.body) and (comment.author == comment.submission.author or comment.distinguished):
            if comment.author.name == "notifier-bot" or comment.author.name == "AutoModerator" or "reminder to participate" in comment.body.lower():
                continue
            post_id = comment.submission.id
            comment_id = post_history.get(post_id)
            if comment_id:                
                print(f"deleting comment ({post_id},{comment_id.decode()})")
                reddit.comment(comment_id.decode()).delete()
                post_history.delete(post_id)
            if not subscriptions.exists(comment.submission.id):
                continue
            print(f"solved found ({comment.submission.id}, {comment.id})")
            post_id = comment.submission.id
            subject = "Potential answer found!"
            message = ("Hello,\n\nI've detected that a post you're subscribed to might have been solved. " 
                        "[Here's]({}) the possible answer.")
            if (isinstance(comment.parent(), Comment)):
                message = message.format(comment.parent().permalink+'?context=1000')
            else:
                message = message.format(comment.submission.permalink)

            for user in subscriptions.lrange(post_id, 0, subscriptions.llen(post_id)-1):
                user = reddit.redditor(user.decode())
                print(f"notifying u/{user}")
                user.message(subject, message)
            subscriptions.delete(post_id)

if __name__ == "__main__":
    print("logging in...")
    reddit = praw.Reddit(user_agent=os.environ.get('USER_AGENT'), 
                             client_id=os.environ.get('CLIENT_ID'), client_secret=os.environ.get('CLIENT_SECRET'), 
                             username=os.environ.get('USER_NAME'), password=os.environ.get('PASSWORD'))
    subreddit = reddit.subreddit("tipofmytongue+notifierbottest")
    inbox = reddit.inbox

    posts = multiprocessing.Process(target=get_posts, args=(reddit, subreddit), name="posts")
    messages = multiprocessing.Process(target=get_messages, args=(reddit, inbox), name="messages")
    comments = multiprocessing.Process(target=get_comments, args=(reddit, subreddit), name="comments")

    for process in posts, messages, comments:
        process.start()

    for process in posts, messages, comments:
        process.join()