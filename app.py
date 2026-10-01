import os
import sqlite3
import time
from datetime import datetime
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, Response
from dotenv import load_dotenv
import subprocess

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default_secret_fallback')
CLUSTER_INI = '/root/.klei/DoNotStarveTogether/Cluster_1/cluster.ini'

def get_login_password():
    # 聊天室密码实时跟随游戏加入密码：cluster.ini 由游戏服务器在启动或修改设置时重写
    try:
        with open(CLUSTER_INI, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.lower().startswith('cluster_password') and '=' in line:
                    value = line.split('=', 1)[1].strip()
                    if value:
                        return value
    except OSError:
        pass
    return os.environ.get('CHAT_PASSWORD', '')

DB_PATH = '/root/dstweb/chat.db'
SCREEN_SESSION = os.environ.get('DST_SCREEN_SESSION', 'DST_Cluster_1_Master')
LOCKED_IPS = {}

def locked_response(locked_until, now):
    minutes = max(1, int((locked_until - now) // 60) + 1)
    return Response('该 IP 已因多次密码错误被临时封锁，请约 %d 分钟后再试。' % minutes, mimetype='text/plain; charset=utf-8'), 429

@app.after_request
def add_header(r):
    r.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return r

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS messages
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  ts REAL,
                  timestamp TEXT,
                  sender TEXT,
                  content TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS sessions
                 (username TEXT PRIMARY KEY,
                  token TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS login_attempts
                 (ip TEXT PRIMARY KEY,
                  attempts INTEGER,
                  lock_until REAL)''')
    conn.commit()
    conn.close()

init_db()

@app.before_request
def check_session():
    if request.endpoint in ['chat', 'api_messages', 'api_players']:
        if session.get('logged_in'):
            username = session.get('username')
            token = session.get('token')
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT token FROM sessions WHERE username=?", (username,))
            row = c.fetchone()
            conn.close()
            if not row or row[0] != token:
                session.clear()
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Logged in from another device', 'logout': True}), 401
                return redirect(url_for('login', msg='another_device', v=os.urandom(4).hex()))

@app.route('/')
def index():
    return redirect(url_for('chat', v=os.urandom(4).hex()))

@app.route('/chat')
def chat():
    if not session.get('logged_in'):
        return redirect(url_for('login', v=os.urandom(4).hex()))

    raw_username = session.get('username', 'Unknown')
    sender = 'Admin' if raw_username.lower() == 'host' else raw_username
    return render_template('chat.html', username=sender)

@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None
    if request.args.get('msg') == 'another_device':
        error = '该账号已在另一台设备登录，您已被挤下线。'

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')

        if not username:
            return render_template('login.html', error='请输入用户名')

        ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip and ',' in ip:
            ip = ip.split(',')[0].strip()

        now = time.time()

        locked_until = LOCKED_IPS.get(ip)
        if locked_until is not None:
            if now < locked_until:
                return locked_response(locked_until, now)
            del LOCKED_IPS[ip]

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT attempts, lock_until FROM login_attempts WHERE ip=?", (ip,))
        row = c.fetchone()

        if row and row[1] and now < row[1]:
            conn.close()
            LOCKED_IPS[ip] = row[1]
            return locked_response(row[1], now)

        expected_password = get_login_password()
        if not expected_password:
            conn.close()
            return render_template('login.html', error='服务端未配置登录密码，请检查 CHAT_PASSWORD 或游戏 cluster.ini。')

        if password == expected_password:
            if username.lower() != 'host':
                online_players = get_online_players()
                online_names = [p['name'] for p in online_players]
                if username not in online_names:
                    conn.close()
                    return render_template('login.html', error=f'玩家 {username} 当前未在游戏中，请进入游戏后再登录。')

            c.execute("DELETE FROM login_attempts WHERE ip=?", (ip,))
            LOCKED_IPS.pop(ip, None)
            token = os.urandom(16).hex()
            c.execute("INSERT OR REPLACE INTO sessions (username, token) VALUES (?, ?)", (username, token))
            conn.commit()
            conn.close()

            session['logged_in'] = True
            session['username'] = username
            session['token'] = token
            return redirect(url_for('chat', v=os.urandom(4).hex()))
        else:
            attempts = (row[0] + 1) if row else 1
            lock_until = (now + 7200) if attempts >= 3 else None
            c.execute("INSERT OR REPLACE INTO login_attempts (ip, attempts, lock_until) VALUES (?, ?, ?)", (ip, attempts, lock_until))
            conn.commit()
            conn.close()
            if lock_until:
                if len(LOCKED_IPS) > 1000:
                    for k in [k for k, v in LOCKED_IPS.items() if v <= now]:
                        LOCKED_IPS.pop(k, None)
                LOCKED_IPS[ip] = lock_until
                return locked_response(lock_until, now)
            return render_template('login.html', error=f'密码错误，还有 {3 - attempts} 次机会。')

    return render_template('login.html', error=error)

@app.route('/logout')
def logout():
    username = session.get('username')
    if username:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM sessions WHERE username=?", (username,))
        conn.commit()
        conn.close()
    session.clear()
    return redirect(url_for('login', v=os.urandom(4).hex()))

def get_online_players():
    try:
        lua_cmd = 'for _,v in pairs(TheNet:GetClientTable())do print("WEB".."CHAT|"..tostring(v.userid).."|"..tostring(v.name).."|"..tostring(v.prefab))end'
        subprocess.run(["screen", "-S", SCREEN_SESSION, "-p", "0", "-X", "stuff", lua_cmd + "\n"])
        time.sleep(0.5)

        log_path = '/root/.klei/DoNotStarveTogether/Cluster_1/Master/server_log.txt'
        out = subprocess.run(["tail", "-n", "20", log_path], capture_output=True, text=True, encoding='utf-8', errors='ignore')

        players = []
        seen = set()

        for line in out.stdout.split('\n'):
            line = line.strip()
            if 'WEBCHAT|' in line:
                payload = line.split('WEBCHAT|')[-1]
                parts = payload.split('|')
                if len(parts) >= 3:
                    userid = parts[0].strip()
                    name = parts[1].strip()
                    prefab = parts[2].strip()

                    if prefab and userid not in seen:
                        seen.add(userid)
                        players.append({
                            'userid': userid,
                            'name': name,
                            'prefab': prefab
                        })
        return players
    except Exception as e:
        print('Player fetch error:', e)
        return []

@app.route('/api/players')
def api_players():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    return jsonify(get_online_players())

@app.route('/api/messages', methods=['GET', 'POST'])
def api_messages():
    if not session.get('logged_in'): return jsonify({'error': 'Unauthorized'}), 401

    if request.method == 'POST':
        data = request.json
        content = data.get('content', '').strip()

        raw_username = session.get('username', 'Unknown')
        sender = 'Admin' if raw_username.lower() == 'host' else raw_username

        if not sender or not content:
            return jsonify({'error': 'Missing fields'}), 400

        if len(content) > 100:
            content = content[:100]

        try:
            safe_sender = sender.replace("'", "")
            safe_content = content.replace("'", "")
            lua_cmd = f'TheNet:Announce("[{safe_sender}] {safe_content}")'
            subprocess.run(["screen", "-S", SCREEN_SESSION, "-p", "0", "-X", "stuff", lua_cmd + "\n"])

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            timestamp = datetime.now().strftime('%H:%M')
            c.execute('INSERT INTO messages (ts, timestamp, sender, content) VALUES (?, ?, ?, ?)',
                     (time.time(), timestamp, sender, content))
            conn.commit()

            c.execute('DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 200)')
            conn.commit()
            conn.close()

            return jsonify({'success': True})
        except Exception as e:
            print('Message send error:', e)
            return jsonify({'error': str(e)}), 500

    else:
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            two_hours_ago = time.time() - 7200
            c.execute('DELETE FROM messages WHERE ts < ?', (two_hours_ago,))
            conn.commit()

            c.execute('SELECT timestamp, sender, content FROM messages ORDER BY id ASC')
            rows = c.fetchall()
            conn.close()

            return jsonify([dict(row) for row in rows])
        except Exception as e:
            return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8083)
