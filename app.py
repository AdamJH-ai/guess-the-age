import os
import json
import random
from datetime import datetime
from flask import Flask, render_template, request # Removed unused 'session' import
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet # Recommended for stability

# --- Basic Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'a_super_secret_key_change_me!')
# Use eventlet if installed: pip install eventlet
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")

# === GAME CONFIG ===
GAME_ROUNDS_TOTAL = 5
AVAILABLE_ROUND_TYPES = ['guess_the_age', 'guess_the_year', 'who_didnt_do_it'] # Only these three rounds
MAX_PLAYERS = 8
gta_target_turns = 10
gty_target_turns = 10
wddi_target_turns = 10

# === ROUND DETAILS ===
ROUND_RULES = {
    'guess_the_age': "Guess the celebrity's current age! Score based on difference (lowest wins).",
    'guess_the_year': "Guess the year the event happened! Score based on difference (lowest wins).",
    'who_didnt_do_it': "Identify the option that doesn't fit the question! Score based on correct answers (most wins)."
}
ROUND_DISPLAY_NAMES = {
    'guess_the_age': "Guess The Age",
    'guess_the_year': "Guess The Year",
    'who_didnt_do_it': "Who Didn't Do It?"
}
ROUND_INTRO_DELAY = 8 # Seconds

# === GAME STATE ===
game_state = "waiting"
current_game_round_num = 0
selected_rounds_for_game = []
overall_game_scores = {} # {sid: game_points}
players = {} # {sid: {'name':'N', 'round_score':0, 'gta_guess':None, 'gty_guess':None}} # Removed WDDI fields
main_screen_sid = None

# === GUESS THE AGE STATE ===
gta_celebrities = []; gta_shuffled_celebrities_this_round = []
gta_current_celebrity = None; gta_current_celebrity_index = -1; gta_actual_turns_this_round = 0

# === GUESS THE YEAR STATE ===
gty_questions = []; gty_shuffled_questions_this_round = []
gty_current_question = None; gty_current_question_index = -1; gty_actual_turns_this_round = 0

# === WHO DIDN'T DO IT STATE ===
wddi_questions = [] # Holds all loaded questions
wddi_shuffled_questions_this_round = [] # Holds the 10 questions selected for the current round
wddi_current_question = None # Holds the question data for the current turn
wddi_current_question_index = -1 # Index for the current turn within the round
wddi_actual_turns_this_round = 0 # Number of turns/questions in this specific round (usually 10)
wddi_current_shuffled_options = [] # Holds the shuffled options for the *current* turn
# Note: We will add wddi_current_guess to the players dict later

# === ROOMS ===
MAIN_ROOM = 'main_room'; PLAYERS_ROOM = 'players_room'

# === DATA LOADING ===
def load_guess_the_age_data(filename="celebrities.json"):
    global gta_celebrities; gta_celebrities = []
    try:
        with open(filename, 'r', encoding='utf-8') as f: data = json.load(f)
        print(f"[GTAData] Loaded {len(data)} potential from {filename}")
        today = datetime.today(); processed = []
        for c in data:
            try:
                if not all(k in c for k in ('name','dob','image_url','description')): continue
                dob = datetime.strptime(c['dob'], '%Y-%m-%d')
                c['age'] = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
                processed.append(c)
            except Exception as e: print(f"[GTAData] Skip Err({e}): {c.get('name')}")
        gta_celebrities = processed; print(f"[GTAData] OK: {len(gta_celebrities)}")
    except Exception as e: print(f"[GTAData] Load Fail: {e}")

def load_guess_the_year_data(filename="guess_the_year_questions.json"):
    global gty_questions; gty_questions = []
    try:
        with open(filename, 'r', encoding='utf-8') as f: data = json.load(f)
        print(f"[GTYData] Loaded {len(data)} potential from {filename}")
        gty_questions = [q for q in data if q.get('question') and q.get('year') and isinstance(q.get('year'), int) and q.get('image_url')] # Ensure image_url exists
        print(f"[GTYData] OK: {len(gty_questions)} valid.")
        if not gty_questions: print("[GTYData] WARN: No valid questions.")
    except Exception as e: print(f"[GTYData] Load Fail: {e}")

def load_who_didnt_do_it_data(filename="who_didnt_do_it_questions.json"):
    """Loads questions for the 'Who Didn't Do It?' round."""
    global wddi_questions; wddi_questions = []
    filepath = filename
    # filepath = filename # If file is in the same directory as app.py
    try:
        # Adjust 'filepath' if your json isn't in static/
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        print(f"[WDDI_Data] Loaded {len(data)} potential questions from {filename}")
        processed_questions = []
        for q in data:
            # Validate required fields and structure
            if not all(k in q for k in ('question', 'options', 'correct_answer')) or \
               not isinstance(q['options'], list) or len(q['options']) != 6 or \
               not q['correct_answer'] or q['correct_answer'] not in q['options']:
                print(f"[WDDI_Data] Skipping invalid entry: {q.get('question', 'N/A')}")
                continue
            # Add optional image_url if present, otherwise set to None
            q['image_url'] = q.get('image_url', None)
            processed_questions.append(q)

        wddi_questions = processed_questions
        print(f"[WDDI_Data] OK: {len(wddi_questions)} valid questions loaded.")
        if not wddi_questions:
            print("[WDDI_Data] WARN: No valid questions loaded for 'Who Didn't Do It?'.")
    except FileNotFoundError:
         print(f"[WDDI_Data] ERROR: File not found at {filepath}")
    except json.JSONDecodeError as e:
         print(f"[WDDI_Data] ERROR: Failed to parse JSON in {filename}: {e}")
    except Exception as e:
        print(f"[WDDI_Data] Load Fail: An unexpected error occurred: {e}")

# === HELPERS ===
def update_main_screen_html(target_selector, template_name, context):
    """Renders a template fragment and sends it to the main screen."""
    if main_screen_sid:
        try:
            html_content = render_template(template_name, **context)
            socketio.emit('update_html', {
                'target_selector': target_selector,
                'html': html_content
            }, room=main_screen_sid)
        except Exception as e:
            print(f"ERROR rendering template {template_name}: {e}")

def emit_player_list_update():
    """Sends updated player list HTML."""
    player_names = [p['name'] for p in players.values()]
    update_main_screen_html('#player-list', '_player_list.html', {'player_names': player_names})

def emit_game_state_update():
    """Sends non-HTML game state info (scores, round nums, etc.)."""
    if main_screen_sid:
        scores_list = sorted([{'name': p['name'], 'game_score': overall_game_scores.get(sid, 0)}
                              for sid, p in players.items()], key=lambda x: x['game_score'], reverse=True)
        payload = {
            'game_state': game_state,
            'current_game_round_num': current_game_round_num,
            'game_rounds_total': GAME_ROUNDS_TOTAL,
            'overall_scores': scores_list,
            'current_round_type': selected_rounds_for_game[current_game_round_num-1] if 0 < current_game_round_num <= len(selected_rounds_for_game) else None
        }
        socketio.emit('game_state_update', payload, room=main_screen_sid)

# <<< Corrected Stableford Scoring Logic >>>
def award_game_points(sorted_player_sids_by_round_score):
    global overall_game_scores; num_players = len(sorted_player_sids_by_round_score);
    if num_players == 0: return {}
    print(f"Awarding points for {num_players} players...")
    points_by_rank = {}
    for rank in range(1, num_players + 1): points_by_rank[rank] = (num_players + 1) if rank == 1 and num_players > 1 else (2 if rank == 1 and num_players == 1 else num_players - rank + 1)
    print(f"  Points structure (Rank: Points): {points_by_rank}"); points_awarded_this_round = {}; i = 0
    while i < num_players:
        current_sid = sorted_player_sids_by_round_score[i]; current_player_info = players.get(current_sid)
        if not current_player_info: i += 1; continue
        current_round_score = current_player_info.get('round_score', None); tied_sids = [current_sid]; j = i + 1
        while j < num_players:
            next_sid=sorted_player_sids_by_round_score[j]; next_player_info=players.get(next_sid)
            if not next_player_info or next_player_info.get('round_score', None) != current_round_score: break
            tied_sids.append(next_sid); j += 1
        num_tied = len(tied_sids); rank_start = i + 1; rank_end = i + num_tied
        if num_tied == 1: points = points_by_rank.get(rank_start, 0)
        else: sum_points = sum(points_by_rank.get(r, 0) for r in range(rank_start, rank_end + 1)); points = round(sum_points / num_tied, 1); print(f"  Tie ranks {rank_start}-{rank_end} avg: {points}")
        for tied_sid in tied_sids:
            if tied_sid in overall_game_scores: points_awarded_this_round[tied_sid] = points; overall_game_scores[tied_sid] = overall_game_scores.get(tied_sid, 0) + points; print(f"  - {players.get(tied_sid,{}).get('name','?')} gets {points} pts. Total: {overall_game_scores[tied_sid]}")
        i += num_tied
    return points_awarded_this_round

# Helpers for checking guesses
def check_all_guesses_received_gta(): return all(p.get('gta_current_guess') is not None for p in players.values()) if players else True
def check_all_guesses_received_gty(): return all(p.get('gty_current_guess') is not None for p in players.values()) if players else True
# REMOVED WDDI check

# === ROUTES ===
@app.route('/')
def index(): return render_template('index.html')
@app.route('/main')
def main_screen_route(): return render_template('main_screen.html')

# === SOCKET.IO HANDLERS ===
@socketio.on('connect')
def handle_connect(): print(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    player_sid = request.sid; global main_screen_sid
    if player_sid == main_screen_sid: print("Main Screen disconnected."); main_screen_sid = None; leave_room(MAIN_ROOM, player_sid)
    elif player_sid in players:
        player_name = players.pop(player_sid)['name']; overall_game_scores.pop(player_sid, None)
        print(f"Player {player_name} disconnected."); leave_room(PLAYERS_ROOM, player_sid);
        emit_player_list_update(); emit_game_state_update()
        # Check results conditions for GTA and GTY only
        if game_state == 'guess_age_ongoing' and check_all_guesses_received_gta(): process_guess_age_turn_results()
        elif game_state == 'guess_the_year_ongoing' and check_all_guesses_received_gty(): process_guess_the_year_turn_results()
        elif game_state == 'who_didnt_do_it_ongoing' and check_all_guesses_received_wddi(): process_who_didnt_do_it_turn_results()
    else: print(f"Unregistered client disconnected: {player_sid}")

@socketio.on('register_main_screen')
def handle_register_main_screen():
    global main_screen_sid; player_sid = request.sid
    if main_screen_sid and main_screen_sid != player_sid: print(f"WARN: New main screen {player_sid}.")
    leave_room(PLAYERS_ROOM, player_sid); join_room(MAIN_ROOM, player_sid); main_screen_sid = player_sid
    print(f"Main Screen registered: {main_screen_sid}")
    emit_player_list_update(); emit_game_state_update()

@socketio.on('register_player')
def handle_register_player(data):
    player_sid = request.sid; player_name = str(data.get('name', f'P_{player_sid[:4]}')).strip()[:15] or f'P_{player_sid[:4]}'
    if player_sid == main_screen_sid: return
    if len(players) >= MAX_PLAYERS and player_sid not in players: emit('message', {'data': 'Game full.'}, room=player_sid); return
    if player_sid not in players:
        join_room(PLAYERS_ROOM, player_sid)
        # Initialize only GTA and GTY fields
        players[player_sid] = {'name': player_name, 'round_score': 0, 'gta_current_guess': None, 'gty_current_guess': None, 'wddi_current_guess': None}
        overall_game_scores[player_sid] = 0; print(f"Player registered: {player_name} ({player_sid[:4]})")
        emit('message', {'data': f'Welcome {player_name}!'}, room=player_sid)
    else: players[player_sid]['name'] = player_name; emit('message', {'data': f'Rejoined as {player_name}.'}, room=player_sid)
    emit_player_list_update(); emit_game_state_update()
    # Simplified join mid-game handling
    if game_state.endswith('_ongoing'): emit('message', {'data': 'Game in progress, wait...'}, room=player_sid)
    elif game_state.endswith('_results'): emit('results_on_main_screen', room=player_sid)
    elif game_state == 'overall_game_over': emit('overall_game_over_player', room=player_sid)
    elif game_state == 'waiting': emit('message', {'data': f'Welcome {player_name}! Waiting.'}, room=player_sid)

# === OVERALL GAME FLOW ===
@socketio.on('start_game_request')
def handle_start_overall_game_request():
    global game_state, current_game_round_num, selected_rounds_for_game, overall_game_scores
    if request.sid != main_screen_sid or game_state != "waiting": return
    if not players or not AVAILABLE_ROUND_TYPES: print("ERR: Cannot start."); return
    print(f"Overall Game start request."); game_state = "game_ongoing"; current_game_round_num = 0
    overall_game_scores = {sid: 0 for sid in players};
    num_avail = len(AVAILABLE_ROUND_TYPES)
    if num_avail >= GAME_ROUNDS_TOTAL: selected_rounds_for_game = random.sample(AVAILABLE_ROUND_TYPES, GAME_ROUNDS_TOTAL)
    else: selected_rounds_for_game = (AVAILABLE_ROUND_TYPES * (GAME_ROUNDS_TOTAL // num_avail + 1))[:GAME_ROUNDS_TOTAL]; random.shuffle(selected_rounds_for_game)
    print(f"Selected rounds: {selected_rounds_for_game}"); emit_game_state_update(); socketio.sleep(1); start_next_game_round()

# <<< Dispatcher only calls GTA and GTY >>>
def start_next_game_round():
    global current_game_round_num, game_state
    current_game_round_num += 1; print(f"\n===== Prep Game Rnd {current_game_round_num}/{GAME_ROUNDS_TOTAL} =====")
    if current_game_round_num > GAME_ROUNDS_TOTAL: end_overall_game(); return
    round_type_key = selected_rounds_for_game[current_game_round_num - 1]
    round_type_name = ROUND_DISPLAY_NAMES.get(round_type_key, round_type_key)
    round_rules = ROUND_RULES.get(round_type_key, "No rules.")
    print(f"Round Type: {round_type_name}"); game_state = "round_intro"; emit_game_state_update()
    intro_context = {'game_round_num': current_game_round_num, 'game_rounds_total': GAME_ROUNDS_TOTAL, 'round_type_name': round_type_name, 'round_rules': round_rules }
    update_main_screen_html('#results-area', '_round_intro.html', intro_context)
    print(f"Show intro {ROUND_INTRO_DELAY}s..."); socketio.sleep(ROUND_INTRO_DELAY)
    if game_state != "round_intro": print("WARN: State changed during intro."); return
    # Dispatch to GTA or GTY only
    if round_type_key == 'guess_the_age': setup_guess_age_round()
    elif round_type_key == 'guess_the_year': setup_guess_the_year_round()
    elif round_type_key == 'who_didnt_do_it': setup_who_didnt_do_it_round()
    else: print(f"ERR: Unknown/Removed type {round_type_key}. Skip."); socketio.sleep(1); start_next_game_round()

def end_overall_game():
    global game_state; print("\n***** OVERALL GAME OVER *****"); game_state = "overall_game_over"
    emit_game_state_update(); final_scores = []
    sorted_players = sorted(overall_game_scores.items(), key=lambda item: item[1], reverse=True)
    final_scores = [{'rank': r+1, 'name': players.get(sid, {}).get('name', '?'), 'game_score': score} for r, (sid, score) in enumerate(sorted_players)]
    print("Final Scores:", final_scores)
    update_main_screen_html('#overall-game-over-area', '_overall_game_over.html', {'scores': final_scores})
    socketio.emit('overall_game_over_player', room=PLAYERS_ROOM); print("Sent overall game over notices.")
    socketio.sleep(15); game_state = "waiting"; print("State reset to waiting.")
    if main_screen_sid: emit_game_state_update(); socketio.emit('ready_for_new_game', room=main_screen_sid)

# === GUESS THE AGE LOGIC ===
# (setup_guess_age_round, next_guess_age_turn, handle_submit_gta_guess, process_guess_age_turn_results, end_guess_age_round - Reverted to the state before WDDI was added, includes debug logs)
def setup_guess_age_round():
    global game_state, gta_shuffled_celebrities_this_round, gta_current_celebrity_index, gta_actual_turns_this_round, gta_celebrities, gta_target_turns
    print("--- Setup GTA Round ---"); game_state = "guess_age_ongoing"
    if not gta_celebrities: print("ERR: No celebs for GTA."); start_next_game_round(); return
    for sid in players: players[sid]['round_score'] = 0; players[sid]['gta_current_guess'] = None
    gta_actual_turns_this_round = min(gta_target_turns, len(gta_celebrities)); gta_shuffled_celebrities_this_round = random.sample(gta_celebrities, gta_actual_turns_this_round)
    gta_current_celebrity_index = -1; print(f"GTA Round: {gta_actual_turns_this_round} turns."); emit_game_state_update(); socketio.sleep(0.5); next_guess_age_turn()
def next_guess_age_turn():
    global game_state, gta_current_celebrity, gta_current_celebrity_index, gta_actual_turns_this_round
    gta_current_celebrity_index += 1;
    if gta_current_celebrity_index >= gta_actual_turns_this_round: end_guess_age_round(); return
    game_state = "guess_age_ongoing"; gta_current_celebrity = gta_shuffled_celebrities_this_round[gta_current_celebrity_index]
    for sid in players: players[sid]['gta_current_guess'] = None
    print(f"\n-- GTA Turn {gta_current_celebrity_index + 1}/{gta_actual_turns_this_round} -- Celeb: {gta_current_celebrity['name']}")
    context = {'turn': gta_current_celebrity_index + 1, 'total_turns': gta_actual_turns_this_round,'celebrity': gta_current_celebrity, 'players_status': [{'name': p['name']} for p in players.values()]}
    update_main_screen_html('#round-content-area', '_gta_turn_display.html', context); player_payload = { 'celebrity_name': gta_current_celebrity['name'] }; socketio.emit('gta_player_prompt', player_payload, room=PLAYERS_ROOM)
@socketio.on('submit_gta_guess')
def handle_submit_gta_guess(data):
    player_sid = request.sid;
    if player_sid in players and game_state == "guess_age_ongoing":
        try:
            guess = int(data.get('guess')); assert 0 <= guess <= 120
            if players[player_sid].get('gta_current_guess') is None:
                players[player_sid]['gta_current_guess'] = guess; player_name = players[player_sid]['name']; print(f"GTA Guess {guess} from {player_name}({player_sid[:4]})")
                remaining = sum(1 for p in players.values() if p.get('gta_current_guess') is None); emit('gta_wait_for_guesses', {'waiting_on': remaining}, room=player_sid)
                safe_name_id = player_name.replace('[^a-zA-Z0-9-_]', '_'); socketio.emit('gta_mark_player_guessed', {'safe_id': safe_name_id}, room=main_screen_sid)
                if check_all_guesses_received_gta(): print("All GTA guesses received."); socketio.sleep(0.5); process_guess_age_turn_results()
            else: emit('message', {'data': 'Already guessed.'}, room=player_sid)
        except Exception as e: emit('message', {'data': 'Invalid guess (0-120).'}, room=player_sid); print(f"Invalid GTA guess: {e}")
def process_guess_age_turn_results():
    global game_state; print(f"DEBUG: Entered process_guess_age_turn_results. State: {game_state}");
    if game_state != "guess_age_ongoing": print("DEBUG: Exiting GTA process early."); return; print("--- Processing GTA Turn Results ---");
    results_context = { 'results': [], 'actual_age': None }; print("DEBUG: Defined results_context GTA.");
    if gta_current_celebrity:
        actual_age = gta_current_celebrity['age']; results_context['actual_age'] = actual_age; print(f"Actual Age: {actual_age}"); round_results_list = []
        active_players_copy = list(players.items()); print(f"DEBUG: GTA Processing for {len(active_players_copy)} players.");
        for sid, p_info in active_players_copy:
            print(f"DEBUG: GTA Loop - Player {p_info.get('name', '?')}"); guess = p_info.get('gta_current_guess'); print(f"DEBUG:   -> Guess: {guess}"); score_diff = abs(actual_age - guess) if guess is not None else None; print(f"DEBUG:   -> Diff: {score_diff}");
            if 'round_score' not in p_info: p_info['round_score'] = 0
            if score_diff is not None: p_info['round_score'] = p_info.get('round_score', 0) + score_diff; print(f"DEBUG:   -> New Rnd Score: {p_info['round_score']}")
            result_entry = {'name': p_info.get('name', '?'),'guess': guess if guess is not None else 'N/A','diff': score_diff if score_diff is not None else '-','round_score': p_info.get('round_score', 0)}; round_results_list.append(result_entry); print(f"DEBUG:   -> Appended: {result_entry}")
        print(f"DEBUG: GTA finished loop. List size: {len(round_results_list)}"); results_context['results'] = sorted(round_results_list, key=lambda r: r['diff'] if isinstance(r['diff'], int) else float('inf')); print(f"DEBUG: Final GTA results context: {results_context}")
        update_main_screen_html('#results-area', '_gta_turn_results.html', results_context)
    else: print("Error: process_gta_turn_results - no celeb.")
    socketio.sleep(5);
    if game_state == "guess_age_ongoing": print("DEBUG: Proceeding next GTA turn."); next_guess_age_turn()
    else: print(f"DEBUG: State changed GTA sleep ({game_state}).")
def end_guess_age_round():
    global game_state;
    game_state = "guess_age_results"; # Set state FIRST
    print("\n--- Ending GTA Round ---");
    # Don't emit game state update yet, scores haven't been awarded

    # 1. Determine rankings (lower round_score is better rank)
    active_players = [(sid, p.get('round_score', float('inf'))) for sid, p in players.items()]
    # Sort by score (ascending), then name alphabetically for stable tie ranks
    sorted_by_round = sorted(active_players, key=lambda item: (item[1], players.get(item[0],{}).get('name','')))
    sorted_sids = [item[0] for item in sorted_by_round]

    # <<< Log BEFORE awarding points >>>
    print(f"DEBUG: Overall scores BEFORE award_game_points: {overall_game_scores}")

    # 2. Award game points (This modifies the global overall_game_scores)
    points_awarded = award_game_points(sorted_sids)

    # <<< Log AFTER awarding points >>>
    print(f"DEBUG: Overall scores AFTER award_game_points: {overall_game_scores}")
    print(f"DEBUG: Points awarded this round: {points_awarded}")

    # <<< Emit game state update AFTER scores are calculated >>>
    # This updates the status bar with the latest scores
    emit_game_state_update()

    # 3. Prepare payload using the *updated* global scores for the summary screen
    rankings_this_round = []
    for rank, sid in enumerate(sorted_sids):
        if sid in players: # Check player still exists
            rankings_this_round.append({
                'rank': rank + 1,
                'name': players[sid]['name'],
                'round_score': players[sid]['round_score'],
                'points_awarded': points_awarded.get(sid, 0) # Include points awarded
            })

    # Generate the overall scores list *now* based on the updated global dictionary
    current_overall_scores_list = [{'name': p['name'], 'game_score': overall_game_scores.get(sid, 0)}
                                   for sid, p in players.items()]
    # Sort this list for display consistency (e.g., by score descending)
    current_overall_scores_list.sort(key=lambda x: x['game_score'], reverse=True)


    summary_context = {
        'round_type': ROUND_DISPLAY_NAMES.get('guess_the_age', 'Guess The Age'),
        'rankings': rankings_this_round,
        'overall_scores': current_overall_scores_list # Use the freshly generated list
    }
    print(f"DEBUG: Summary Context being sent: {summary_context}") # Log context

    # Send HTML for summary screen (now includes correct overall scores)
    update_main_screen_html('#results-area', '_round_summary.html', summary_context)
    print("Sent 'round_over_summary' HTML.")

    # 4. Pause and move to the next *game* round
    round_summary_display_time = 12;
    print(f"Waiting {round_summary_display_time}s before next game round...")
    socketio.sleep(round_summary_display_time)

    # Check state hasn't changed during sleep before proceeding
    if game_state == "guess_age_results":
        start_next_game_round()
    else:
        print(f"WARN: Game state changed during round summary sleep ({game_state}). Not proceeding automatically.")

# === GUESS THE YEAR LOGIC ===
# (setup_guess_the_year_round, next_guess_the_year_turn, handle_submit_gty_guess, process_guess_the_year_turn_results, end_guess_the_year_round - Reverted to state before WDDI, includes debug logs)
def setup_guess_the_year_round():
    global game_state, gty_shuffled_questions_this_round, gty_current_question_index, gty_actual_turns_this_round, gty_questions, gty_target_turns
    print("--- Setup GTY Round ---"); game_state = "guess_the_year_ongoing";
    if not gty_questions: print("ERR: No questions GTY."); start_next_game_round(); return
    for sid in players: players[sid]['round_score'] = 0; players[sid]['gty_current_guess'] = None
    gty_actual_turns_this_round = min(gty_target_turns, len(gty_questions)); gty_shuffled_questions_this_round = random.sample(gty_questions, gty_actual_turns_this_round)
    gty_current_question_index = -1; print(f"GTY Round: {gty_actual_turns_this_round} turns."); emit_game_state_update(); socketio.sleep(0.5); next_guess_the_year_turn()
def next_guess_the_year_turn():
    global game_state, gty_current_question, gty_current_question_index, gty_actual_turns_this_round
    gty_current_question_index += 1;
    if gty_current_question_index >= gty_actual_turns_this_round: end_guess_the_year_round(); return
    game_state = "guess_the_year_ongoing"; gty_current_question = gty_shuffled_questions_this_round[gty_current_question_index]
    for sid in players: players[sid]['gty_current_guess'] = None
    print(f"\n-- GTY Turn {gty_current_question_index + 1}/{gty_actual_turns_this_round} -- Q: {gty_current_question['question']}"); print(f"   (Ans: {gty_current_question['year']})")
    context = {'turn': gty_current_question_index + 1, 'total_turns': gty_actual_turns_this_round,'question_data': gty_current_question,'players_status': [{'name': p['name']} for p in players.values()]}
    update_main_screen_html('#round-content-area', '_gty_turn_display.html', context); player_payload = { 'question': gty_current_question['question'] }; socketio.emit('gty_player_prompt', player_payload, room=PLAYERS_ROOM)
@socketio.on('submit_gty_guess')
def handle_submit_gty_guess(data):
    player_sid = request.sid;
    if player_sid in players and game_state == "guess_the_year_ongoing":
        try:
            guess = int(data.get('guess')); assert -10000 <= guess <= datetime.now().year + 100
            if players[player_sid].get('gty_current_guess') is None:
                players[player_sid]['gty_current_guess'] = guess; player_name = players[player_sid]['name']; print(f"GTY Guess {guess} from {player_name}({player_sid[:4]})")
                remaining = sum(1 for p in players.values() if p.get('gty_current_guess') is None); emit('gty_wait_for_guesses', {'waiting_on': remaining}, room=player_sid)
                safe_name_id = player_name.replace('[^a-zA-Z0-9-_]', '_'); socketio.emit('gty_mark_player_guessed', {'safe_id': safe_name_id}, room=main_screen_sid)
                if check_all_guesses_received_gty(): print("All GTY guesses received."); socketio.sleep(0.5); process_guess_the_year_turn_results()
            else: emit('message', {'data': 'Already guessed.'}, room=player_sid)
        except Exception as e: emit('message', {'data': 'Invalid year.'}, room=player_sid); print(f"Invalid GTY guess: {e}")
def process_guess_the_year_turn_results():
    global game_state; print(f"DEBUG: Entered process_gty_turn_results. State: {game_state}");
    if game_state != "guess_the_year_ongoing": print("DEBUG: Exiting GTY process early."); return; print("--- Processing GTY Turn Results ---");
    results_context = { 'results': [], 'correct_year': None, 'question_text': '' }; print("DEBUG: Defined results_context GTY.");
    if gty_current_question:
        correct_year = gty_current_question['year']; results_context['correct_year'] = correct_year; results_context['question_text'] = gty_current_question['question']; print(f"Actual Year: {correct_year}"); round_results_list = []
        active_players_copy = list(players.items()); print(f"DEBUG: GTY Processing for {len(active_players_copy)} players.");
        for sid, p_info in active_players_copy:
            print(f"DEBUG: GTY Loop - Player {p_info.get('name', '?')}"); guess = p_info.get('gty_current_guess'); print(f"DEBUG:   -> Guess: {guess}"); score_diff = abs(correct_year - guess) if guess is not None else None; print(f"DEBUG:   -> Diff: {score_diff}");
            if 'round_score' not in p_info: p_info['round_score'] = 0
            if score_diff is not None: p_info['round_score'] = p_info.get('round_score', 0) + score_diff; print(f"DEBUG:   -> New Rnd Score: {p_info['round_score']}")
            result_entry = {'name': p_info.get('name', '?'),'guess': guess if guess is not None else 'N/A','diff': score_diff if score_diff is not None else '-','round_score': p_info.get('round_score', 0)}; round_results_list.append(result_entry); print(f"DEBUG:   -> Appended GTY: {result_entry}")
        print(f"DEBUG: GTY finished loop. List size: {len(round_results_list)}"); results_context['results'] = sorted(round_results_list, key=lambda r: r['diff'] if isinstance(r['diff'], int) else float('inf')); print(f"DEBUG: Final GTY results context: {results_context}")
        update_main_screen_html('#results-area', '_gty_turn_results.html', results_context)
    else: print("Error: process_gty_turn_results - no question.")
    socketio.sleep(5);
    if game_state == "guess_the_year_ongoing": print("DEBUG: Proceeding next GTY turn."); next_guess_the_year_turn()
    else: print(f"DEBUG: State changed GTY sleep ({game_state}).")
def end_guess_the_year_round():
    global game_state;
    game_state = "guess_the_year_results"; # Set state FIRST
    print("\n--- Ending GTY Round ---");
    # Don't emit game state update yet, scores haven't been awarded

    # 1. Determine rankings (lower round_score is better rank)
    active_players = [(sid, p.get('round_score', float('inf'))) for sid, p in players.items()]
    # Sort by score (ascending), then name alphabetically for stable tie ranks
    sorted_by_round = sorted(active_players, key=lambda item: (item[1], players.get(item[0],{}).get('name','')))
    sorted_sids = [item[0] for item in sorted_by_round]

    # <<< Log BEFORE awarding points >>>
    print(f"DEBUG: Overall scores BEFORE award_game_points: {overall_game_scores}")

    # 2. Award game points (Modifies global overall_game_scores)
    points_awarded = award_game_points(sorted_sids)

    # <<< Log AFTER awarding points >>>
    print(f"DEBUG: Overall scores AFTER award_game_points: {overall_game_scores}")
    print(f"DEBUG: Points awarded this round: {points_awarded}")

    # <<< Emit game state update AFTER scores are calculated >>>
    emit_game_state_update() # Updates status bar

    # 3. Prepare payload using updated scores
    rankings_this_round = []
    for rank, sid in enumerate(sorted_sids):
        if sid in players:
            rankings_this_round.append({
                'rank': rank + 1,
                'name': players[sid]['name'],
                'round_score': players[sid]['round_score'],
                'points_awarded': points_awarded.get(sid, 0)
            })

    current_overall_scores_list = [{'name': p['name'], 'game_score': overall_game_scores.get(sid, 0)}
                                   for sid, p in players.items()]
    current_overall_scores_list.sort(key=lambda x: x['game_score'], reverse=True) # Sort display list

    summary_context = {
        'round_type': ROUND_DISPLAY_NAMES.get('guess_the_year', 'Guess The Year'),
        'rankings': rankings_this_round,
        'overall_scores': current_overall_scores_list
    }
    print(f"DEBUG: Summary Context being sent: {summary_context}")

    # Send HTML for summary screen
    update_main_screen_html('#results-area', '_round_summary.html', summary_context)
    print("Sent 'round_over_summary' HTML.")

    # 4. Pause and move to next game round
    round_summary_display_time = 12;
    print(f"Waiting {round_summary_display_time}s before next game round...")
    socketio.sleep(round_summary_display_time)

    if game_state == "guess_the_year_results":
        start_next_game_round()
    else:
         print(f"WARN: Game state changed during round summary sleep ({game_state}). Not proceeding.")

# === WHO DIDN'T DO IT LOGIC ===
# Helper to check if all players have submitted their guess for the current WDDI turn
def check_all_guesses_received_wddi():
    """Checks if all connected players have submitted a WDDI guess for the current turn."""
    if not players:
        return True # No players, so technically all received
    return all(p.get('wddi_current_guess') is not None for p in players.values())

def setup_who_didnt_do_it_round():
    """Sets up the state for a 'Who Didn't Do It?' round."""
    global game_state, wddi_shuffled_questions_this_round, wddi_current_question_index
    global wddi_actual_turns_this_round, wddi_questions, wddi_target_turns
    print("--- Setup WDDI Round ---")
    game_state = "who_didnt_do_it_ongoing" # Set the specific game state

    if not wddi_questions:
        print("ERROR: No questions loaded for 'Who Didn't Do It?'. Skipping round.")
        start_next_game_round() # Skip to next round if no data
        return

    # Reset round scores and guesses for all players
    for sid in players:
        players[sid]['round_score'] = 0 # Reset round score (higher is better here)
        players[sid]['wddi_current_guess'] = None # Reset guess for the round start

    # Select questions for the round
    wddi_actual_turns_this_round = min(wddi_target_turns, len(wddi_questions))
    wddi_shuffled_questions_this_round = random.sample(wddi_questions, wddi_actual_turns_this_round)
    wddi_current_question_index = -1 # Start before the first turn

    print(f"WDDI Round starting with {wddi_actual_turns_this_round} questions.")
    emit_game_state_update() # Update main screen status bar
    socketio.sleep(0.5) # Short pause before first turn
    next_who_didnt_do_it_turn() # Start the first turn

def next_who_didnt_do_it_turn():
    """Advances to the next turn/question in the WDDI round."""
    global game_state, wddi_current_question, wddi_current_question_index
    global wddi_shuffled_questions_this_round, wddi_actual_turns_this_round
    global wddi_current_shuffled_options # Store shuffled options for validation

    wddi_current_question_index += 1

    # Check if round is over
    if wddi_current_question_index >= wddi_actual_turns_this_round:
        end_who_didnt_do_it_round() # All questions asked, end the round
        return

    game_state = "who_didnt_do_it_ongoing" # Ensure state is correct
    wddi_current_question = wddi_shuffled_questions_this_round[wddi_current_question_index]

    # Clear previous guesses for all players
    for sid in players:
        players[sid]['wddi_current_guess'] = None

    # --- Prepare options and shuffle them ---
    original_options = list(wddi_current_question['options']) # Make a copy
    wddi_current_shuffled_options = original_options # Assign before shuffling for context
    random.shuffle(wddi_current_shuffled_options) # Shuffle the list in place

    print(f"\n-- WDDI Turn {wddi_current_question_index + 1}/{wddi_actual_turns_this_round} --")
    print(f"   Q: {wddi_current_question['question']}")
    # print(f"   DEBUG: Shuffled Options: {wddi_current_shuffled_options}") # Optional debug log
    print(f"   Correct Answer: {wddi_current_question['correct_answer']}") # For server log/debug

    # --- Send data to Main Screen ---
    # Context for the main screen display template (_wddi_turn_display.html)
    main_screen_context = {
        'turn': wddi_current_question_index + 1,
        'total_turns': wddi_actual_turns_this_round,
        'question_text': wddi_current_question['question'],
        'image_url': wddi_current_question.get('image_url'), # Include image_url if present
        'shuffled_options': wddi_current_shuffled_options, # Send shuffled options
        'players_status': [{'name': p['name']} for p in players.values()] # For showing who hasn't guessed
    }
    # Assuming you have/will create '_wddi_turn_display.html' in templates/
    update_main_screen_html('#round-content-area', '_wddi_turn_display.html', main_screen_context)

    # --- Send data to Player Controllers ---
    # Payload for the player devices (index.html's JS)
    player_payload = {
        'question': wddi_current_question['question'],
        'shuffled_options': wddi_current_shuffled_options # Send the same shuffled list
        # image_url could be sent here too if players need to see it on their device
    }
    # We need a unique event name for this round's player prompt
    socketio.emit('wddi_player_prompt', player_payload, room=PLAYERS_ROOM)
    print("   Sent question and shuffled options to players.")

@socketio.on('submit_wddi_guess')
def handle_submit_wddi_guess(data):
    """Handles a player submitting their guess for the current WDDI turn."""
    player_sid = request.sid
    if player_sid not in players or game_state != "who_didnt_do_it_ongoing":
        print(f"WARN: Guess rejected from {player_sid[:4]}. State: {game_state}")
        return # Ignore if player not registered or not in the correct game state

    guess_text = data.get('guess_text') # Expecting the text of the chosen option

    # Basic validation: is the guess one of the options sent?
    if not guess_text or guess_text not in wddi_current_shuffled_options:
         emit('message', {'data': 'Invalid selection.'}, room=player_sid)
         print(f"WDDI Invalid guess received: '{guess_text}' from {players[player_sid]['name']}")
         return

    if players[player_sid].get('wddi_current_guess') is None:
        # Store the submitted text as the guess
        players[player_sid]['wddi_current_guess'] = guess_text
        player_name = players[player_sid]['name']
        print(f"WDDI Guess '{guess_text}' received from {player_name}({player_sid[:4]})")

        # Notify player their guess was received (optional)
        # emit('wddi_wait_for_others', room=player_sid) # Or similar feedback

        # Update main screen to show player has guessed (optional, good UI)
        safe_name_id = player_name.replace('[^a-zA-Z0-9-_]', '_') # Create a CSS-safe ID
        socketio.emit('wddi_mark_player_guessed', {'safe_id': safe_name_id}, room=main_screen_sid)

        # Check if all players have now guessed
        if check_all_guesses_received_wddi():
            print("   All WDDI guesses received.")
            socketio.sleep(0.5) # Brief pause before showing results
            process_who_didnt_do_it_turn_results()
    else:
        # Player already submitted a guess for this turn
        emit('message', {'data': 'You already guessed for this question.'}, room=player_sid)
        print(f"WDDI Duplicate guess attempt from {players[player_sid]['name']}")

def process_who_didnt_do_it_turn_results():
    """Processes guesses, calculates scores, and sends results for a WDDI turn."""
    global game_state
    print(f"--- Processing WDDI Turn Results (Index: {wddi_current_question_index}) ---")
    if game_state != "who_didnt_do_it_ongoing" or not wddi_current_question:
        print(f"WARN: Skipping WDDI results processing. State: {game_state}, Question: {wddi_current_question is not None}")
        return # Avoid processing if state changed or question missing

    game_state = "who_didnt_do_it_results_display" # Temp state while showing results

    correct_answer_text = wddi_current_question['correct_answer']
    turn_results_list = []

    print(f"   Correct Answer was: '{correct_answer_text}'")

    active_players_copy = list(players.items()) # Copy to avoid issues if player disconnects during loop
    for sid, p_info in active_players_copy:
        guess = p_info.get('wddi_current_guess')
        was_correct = (guess == correct_answer_text)
        turn_score = 1 if was_correct else 0

        # Update the player's *round score* (cumulative correct answers)
        if 'round_score' not in p_info: p_info['round_score'] = 0 # Ensure exists
        p_info['round_score'] += turn_score
        print(f"   - Player: {p_info['name']}, Guess: '{guess}', Correct: {was_correct}, New Round Score: {p_info['round_score']}")

        turn_results_list.append({
            'name': p_info['name'],
            'guess': guess if guess is not None else 'N/A',
            'is_correct': was_correct,
            'round_score': p_info['round_score'] # Cumulative round score
        })

    # Sort results (e.g., by correctness then name) for display
    turn_results_list.sort(key=lambda x: (-int(x['is_correct']), x['name']))

    # --- Send results to Main Screen ---
    # Context for the results template (_wddi_turn_results.html - Needs creating)
    results_context = {
        'question_text': wddi_current_question['question'],
        'image_url': wddi_current_question.get('image_url'),
        'shuffled_options': wddi_current_shuffled_options, # Show options again
        'correct_answer': correct_answer_text,
        'results': turn_results_list, # List of player results for the turn
        'turn': wddi_current_question_index + 1,
        'total_turns': wddi_actual_turns_this_round
    }
    # NOTE: You will need to create a '_wddi_turn_results.html' template file!
    update_main_screen_html('#results-area', '_wddi_turn_results.html', results_context)
    print(f"   Sent WDDI turn results to main screen.")
    # Send simple notification to players that results are shown
    socketio.emit('results_on_main_screen', room=PLAYERS_ROOM)

    # Pause to show results
    turn_results_display_time = 7 # Seconds to show turn results
    socketio.sleep(turn_results_display_time)

    # Check state hasn't changed during sleep before proceeding
    if game_state == "who_didnt_do_it_results_display":
        print(f"   Proceeding to next WDDI turn/end of round.")
        next_who_didnt_do_it_turn() # Move to the next turn
    else:
        print(f"WARN: Game state changed during WDDI results sleep ({game_state}). Not proceeding automatically.")


def end_who_didnt_do_it_round():
    """Finalizes the WDDI round, awards game points, and transitions."""
    global game_state
    game_state = "who_didnt_do_it_results" # Final round results state
    print("\n--- Ending WDDI Round ---")

    # 1. Determine rankings based on round_score (higher is better for WDDI)
    active_players = [(sid, p.get('round_score', 0)) for sid, p in players.items()]
    # Sort by score (descending), then name alphabetically for stable tie ranks
    sorted_by_round = sorted(active_players, key=lambda item: (-item[1], players.get(item[0],{}).get('name','')))
    sorted_sids = [item[0] for item in sorted_by_round]
    print(f"   WDDI Round Ranks (SID, Score): {sorted_by_round}")

    # 2. Award Stableford game points (using existing helper)
    print(f"   Overall scores BEFORE award_game_points: {overall_game_scores}")
    # Pass the SIDs sorted by rank (higher score = better rank for WDDI)
    points_awarded = award_game_points(sorted_sids)
    print(f"   Overall scores AFTER award_game_points: {overall_game_scores}")
    print(f"   Points awarded this round: {points_awarded}")

    # 3. Emit game state update AFTER scores are calculated (updates status bar)
    emit_game_state_update()

    # 4. Prepare payload for the round summary screen using updated scores
    rankings_this_round = []
    for rank, sid in enumerate(sorted_sids):
        if sid in players: # Check player still exists
            rankings_this_round.append({
                'rank': rank + 1,
                'name': players[sid]['name'],
                'round_score': players[sid]['round_score'], # Show number correct
                'points_awarded': points_awarded.get(sid, 0)
            })

    # Get the latest overall scores for the summary display
    current_overall_scores_list = [{'name': p['name'], 'game_score': overall_game_scores.get(sid, 0)}
                                   for sid, p in players.items()]
    current_overall_scores_list.sort(key=lambda x: x['game_score'], reverse=True) # Sort for display

    summary_context = {
        'round_type': ROUND_DISPLAY_NAMES.get('who_didnt_do_it', 'Who Didn\'t Do It?'),
        'rankings': rankings_this_round, # WDDI round results (higher score = better)
        'overall_scores': current_overall_scores_list # Updated overall game scores
    }
    print(f"   Summary Context being sent: {summary_context}")

    # Use the existing _round_summary.html template
    update_main_screen_html('#results-area', '_round_summary.html', summary_context)
    print("   Sent 'round_over_summary' HTML.")

    # 5. Pause and move to the next game round
    round_summary_display_time = 12 # Seconds
    print(f"   Waiting {round_summary_display_time}s before next game round...")
    socketio.sleep(round_summary_display_time)

    # Check state hasn't changed during sleep before proceeding
    if game_state == "who_didnt_do_it_results":
        start_next_game_round() # Trigger the overall game flow handler
    else:
        print(f"WARN: Game state changed during WDDI summary sleep ({game_state}). Not proceeding automatically.")

# === MAIN EXECUTION ===
if __name__ == '__main__':
    print("Loading round data...");
    load_guess_the_age_data()
    load_guess_the_year_data()
    load_who_didnt_do_it_data()
    print("Starting Flask-SocketIO server..."); use_debug = False
    socketio.run(app, host='0.0.0.0', port=5000, debug=use_debug)
    print("Server stopped.")