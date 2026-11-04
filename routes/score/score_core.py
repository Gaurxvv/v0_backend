from flask import request, jsonify
from models import Score, Match, db, Player, Team
from . import score_bp
from socket_instance import socketio
from flask_cors import cross_origin
from services.outcome_service import OutcomeService


# ---------------------------------------------------------------------------
# POST /update-score
# Supports both regular (score-based) and special outcomes (walkover, etc.)
# ---------------------------------------------------------------------------

@score_bp.route('/update-score', methods=['POST'])
def update_score():
    print("\n=== Starting update_score endpoint ===")
    data = request.get_json()
    print(f"Received request data: {data}")

    match_id     = data.get('match_id')
    score_input  = data.get('score', '0-0')
    tournament_id = data.get('tournament_id')
    final        = data.get('final', False)
    override     = data.get('override', False)
    outcome_type = data.get('outcome_type', 'regular')
    winner_team_id = data.get('winner_team_id')   # required for walkover / forfeit
    details      = data.get('details')

    if not all(k in data for k in ['match_id', 'tournament_id']):
        return jsonify({"error": "match_id and tournament_id are required"}), 400

    try:
        team1_score, team2_score = map(int, score_input.split('-'))
    except (ValueError, AttributeError):
        return jsonify({'error': 'Score format is invalid. Use "{teamA score}-{teamB score}"'}), 400

    try:
        result = OutcomeService.apply_outcome(
            match_id=match_id,
            outcome_type=outcome_type,
            team1_score=team1_score,
            team2_score=team2_score,
            winner_team_id=winner_team_id,
            details=details,
            final=final,
            override=override,
        )

        response = {
            'message':         'Scores updated successfully',
            'match_id':        match_id,
            'team1_id':        result['team1_id'],
            'team1_score':     result['team1_score'],
            'team2_id':        result['team2_id'],
            'team2_score':     result['team2_score'],
            'is_final':        result['is_final'],
            'status':          result['status'],
            'outcome_type':    result['outcome_type'],
            'outcome_details': result['outcome_details'],
            'winner_team_id':  result['winner_team_id'],
            'tournament_id':   tournament_id,
        }

        socketio.emit('score_update', response, namespace='/scores')
        print(f"\nSending response: {response}")
        return jsonify(response), 200

    except LookupError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# GET /team-score
# ---------------------------------------------------------------------------

@score_bp.route('/team-score', methods=['GET'])
def get_team_score():
    team_id = request.args.get('team_id')
    if not team_id:
        return jsonify({"error": "Team ID is required"}), 400

    scores = Score.query.filter_by(team_id=team_id).all()
    score_data = [{'match_id': s.match_id, 'score': s.score} for s in scores]
    return jsonify(score_data), 200


# ---------------------------------------------------------------------------
# GET /score/match  — returns outcome_type and outcome_details
# ---------------------------------------------------------------------------

@score_bp.route('/score/match', methods=['GET', 'OPTIONS'])
@cross_origin()
def get_match_score():
    if request.method == 'OPTIONS':
        return '', 200

    try:
        print("\n=== Starting get_match_score endpoint ===")
        match_id      = request.args.get('match_id')
        tournament_id = request.args.get('tournament_id')

        if not match_id or not tournament_id:
            return jsonify({"error": "match_id and tournament_id are required"}), 400

        match = db.session.get(Match, int(match_id))
        if not match:
            return jsonify({"error": f"Match with ID {match_id} not found"}), 404

        team1 = Team.query.filter_by(team_id=match.team1_id).first()
        team2 = Team.query.filter_by(team_id=match.team2_id).first()

        if not team1 or not team2:
            return jsonify({"error": "One or both teams not found"}), 404

        def player_info(team):
            players = []
            for attr in ('player1', 'player2'):
                p = getattr(team, attr, None)
                if p:
                    players.append({
                        'id':         p.id,
                        'uuid':       p.uuid,
                        'first_name': p.first_name,
                        'last_name':  p.last_name,
                        'checked_in': p.checked_in,
                    })
            return players

        scores = Score.query.filter_by(match_id=match_id).all()
        team1_score = next((s.score for s in scores if s.team_id == match.team1_id), 0)
        team2_score = next((s.score for s in scores if s.team_id == match.team2_id), 0)

        response = {
            'match_id':        match.id,
            'tournament_id':   tournament_id,
            'team1': {
                'team_id': team1.team_id,
                'name':    team1.name,
                'players': player_info(team1),
                'score':   team1_score,
            },
            'team2': {
                'team_id': team2.team_id,
                'name':    team2.name,
                'players': player_info(team2),
                'score':   team2_score,
            },
            'status':          match.status,
            'is_final':        match.is_final,
            'winner_team_id':  match.winner_team_id,
            'outcome_type':    match.outcome_type or 'regular',
            'outcome_details': match.outcome_details,
        }

        return jsonify(response), 200

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# GET /score
# ---------------------------------------------------------------------------

@score_bp.route('/score', methods=['GET'])
def get_scores():
    tournament_id = request.args.get('tournament_id')
    if not tournament_id:
        return jsonify({"error": "Tournament ID is required"}), 400

    matches   = Match.query.filter_by(tournament_id=tournament_id).all()
    match_ids = [m.id for m in matches]
    scores    = Score.query.filter(Score.match_id.in_(match_ids)).all()

    score_data = [{'match_id': s.match_id, 'team_id': s.team_id, 'score': s.score} for s in scores]
    return jsonify(score_data), 200
