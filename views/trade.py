# views/trade.py
import sys
from flask import Blueprint, render_template, request, redirect, url_for, flash, abort, session
from flask_login import login_required, current_user
from models import db
from models.user_models import Users
from models.trade_models import Generation, Group, PLRecord, Request, Accept
from datetime import datetime, timezone, timedelta, date
import yfinance as yf
import pandas as pd
import pytz
import logging
from zoneinfo import ZoneInfo
from sqlalchemy import func
from sqlalchemy.orm.attributes import flag_modified


sys.stdout.reconfigure(encoding='utf-8')  # 日本語の文字化け防止


trade_bp = Blueprint('trade', __name__)
logging.basicConfig(level=logging.INFO)
FIXED_FEE = 500.0  # 固定手数料

# =============================================================================
# 期・グループ管理／表示系エンドポイント（Generation, Group）
# =============================================================================

@trade_bp.route('/generation_edit', methods=['GET', 'POST'])
@login_required
def generation_edit():
    """期の追加/削除 (admin限定)"""
    if current_user.role != 'admin':
        return "権限がありません", 403

    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            new_name = request.form.get('new_generation_name', '').strip()
            if not new_name:
                return "期名が空です", 400
            new_gen = Generation(generation_name=new_name)
            db.session.add(new_gen)
            db.session.commit()
            return redirect(url_for('auth.unified_dashboard'))
        elif action == 'delete':
            gen_id = request.form.get('target_generation_id')
            gen = Generation.query.get(gen_id)
            if gen:
                # 子テーブルの削除：依存関係のある Accept, Request, PLRecord, Group, Users の順で削除
                db.session.query(Accept).filter_by(generation_id=gen_id).delete(synchronize_session=False)
                db.session.flush()
                db.session.query(Request).filter_by(generation_id=gen_id).delete(synchronize_session=False)
                db.session.flush()
                db.session.query(PLRecord).filter_by(generation_id=gen_id).delete(synchronize_session=False)
                db.session.flush()
                db.session.query(Group).filter_by(generation_id=gen_id).delete(synchronize_session=False)
                db.session.flush()
                db.session.query(Users).filter_by(generation_id=gen_id).delete(synchronize_session=False)
                db.session.flush()
                # 最後に Generation を削除
                db.session.delete(gen)
                db.session.commit()
            return redirect(url_for('auth.unified_dashboard'))
        elif action == 'update_activeness':
            gen_id = request.form.get('target_generation_id')
            new_activeness = request.form.get('new_activeness')
            gen = Generation.query.get(gen_id)
            if gen and new_activeness is not None:
                gen.activeness = int(new_activeness)
                db.session.commit()
            return redirect(url_for('trade.generation_edit'))
        return "不正なアクション", 400
    else:
        all_generations = Generation.query.all()
        return render_template('generation_edit.html', all_generations=all_generations)


@trade_bp.route('/generation/<int:generation_id>/groups', methods=['GET', 'POST'])
@login_required
def generation_groups(generation_id):
    """
    ある期(generation_id)のグループ一覧と、各グループのPL推移グラフ（新PLRecordのpl_dataから算出）を表示。
    POSTリクエストの場合は、管理者がアクティブ期のPLを更新するアクションを追加。
    """
    from datetime import datetime
    import pytz
    import logging

    # 期を取得
    gen = Generation.query.get_or_404(generation_id)

    # ---------------------------
    # (A) POSTリクエスト → PL更新
    # ---------------------------
    if request.method == 'POST':
        # 1) 管理者かどうかチェック
        if current_user.role != 'admin':
            flash('権限がありません', 'danger')
            return redirect(url_for('trade.generation_groups', generation_id=generation_id))

        # 2) 期がアクティブかどうかチェック
        if gen.activeness != 1:
            flash('アクティブではない期のPLは更新できません。', 'warning')
            return redirect(url_for('trade.generation_groups', generation_id=generation_id))

        # 3) CLIコマンド「update_pl」相当のロジックを、この期だけに適用して実行
        tokyo_tz = pytz.timezone("Asia/Tokyo")
        today = datetime.now(tokyo_tz).date()
        today_str = today.strftime("%Y%m%d")
        logging.info(f"WEB更新 - generation {generation_id} のPLを更新します: 当日 {today_str}")

        # この期に関連するPLRecord（= Generation.activeness==1 はすでにチェック済み）
        pl_records = PLRecord.query.filter_by(generation_id=generation_id).all()

        for record in pl_records:
            ticker = fix_ticker(record.ticker)
            pl_data = record.pl_data if record.pl_data else {}

            # 当日エントリが存在しない場合、前日分をコピーして作成
            if today_str not in pl_data:
                prev_entry = get_previous_day_entry(pl_data, today_str)
                pl_data[today_str] = prev_entry.copy()  # 必要に応じて .copy()
                logging.info(f"[{ticker}] 当日エントリが無かったため前日コピーを作成 (Gen {generation_id})")

            # 当日エントリ更新
            entry = pl_data[today_str]
            try:
                old_close = entry.get("close_price", None)
                new_close = get_close_price_for_day(ticker, today)
                entry["close_price"] = new_close

                transaction_price = entry.get("transaction_price", 0)
                holding_quantity = entry.get("holding_quantity", 0)
                new_holding_pl = (new_close - transaction_price) * holding_quantity
                entry["holding_pl"] = new_holding_pl

                pl_data[today_str] = entry
                record.pl_data = pl_data
                flag_modified(record, "pl_data")
                db.session.add(record)

                logging.info(
                    f"[{ticker}] old_close={old_close}, new_close={new_close}, "
                    f"tx_price={transaction_price}, qty={holding_quantity}, holding_pl={new_holding_pl}"
                )
            except Exception as e:
                logging.error(f"[{ticker}] 更新中にエラー発生: {str(e)}")
                continue

        # データベースにコミット
        try:
            db.session.commit()
            flash("PLの更新が完了しました。", "success")
            logging.info(f"WEB更新 - generation {generation_id}: PL更新完了")
        except Exception as e:
            db.session.rollback()
            logging.error(f"WEB更新 - コミットエラー: {str(e)}")
            flash("PLの更新中にエラーが発生しました。", "danger")

        return redirect(url_for('trade.generation_groups', generation_id=generation_id))

    # ---------------------------
    # (B) GETリクエスト → もとの一覧画面表示
    # ---------------------------
    groups = Group.query.filter_by(generation_id=generation_id).order_by(Group.group_id).all()

    group_list_with_pl = []
    for g in groups:
        # 最新PLは、各グループ内の全銘柄の最新の pl_data を合算して算出
        total_pl = get_group_latest_pl(generation_id, g.group_id)
        group_list_with_pl.append({
            'group_id': g.group_id,
            'group_name': g.group_name,
            'total_pl': total_pl
        })

    # グラフ用データ：各グループの pl_data（date_str, total_pl のリスト）
    chart_datasets = []
    unique_dates = set()
    group_data_map = {}

    for g in groups:
        history = get_group_pl_history(generation_id, g.group_id)
        group_data_map[g.group_id] = {
            'group_name': g.group_name,
            'pl_history': history  # list of (date_str, total_pl)
        }
        for date_str, _pl in history:
            unique_dates.add(date_str)

    unique_dates = sorted(list(unique_dates))

    import random
    for g in groups:
        data = []
        # 辞書形式に変換（キー：日付、値：PL）
        history = dict(group_data_map[g.group_id]['pl_history'])
        # 各 unique_dates について、値がなければ直前の値または0を補完
        # ※unique_datesより前の日付は考慮しない前提です
        # なお、history_keysはこのグループの記録済み日付のリスト（昇順）
        history_keys = sorted(history.keys())
        for d in unique_dates:
            if d in history:
                value = history[d]
            else:
                # dより前の日付で最新の値を探す
                value = 0  # デフォルトは0
                for past_date in reversed(history_keys):
                    if past_date < d:
                        value = history[past_date]
                        break
            data.append(value)
        r = random.randint(50, 200)
        g_ = random.randint(50, 200)
        b = random.randint(50, 200)
        color_str = f"rgba({r},{g_},{b},1)"
        chart_datasets.append({
            'label': group_data_map[g.group_id]['group_name'],
            'data': data,
            'borderColor': color_str,
            'fill': False
        })

    return render_template(
        'groups_list.html',
        generation_id=generation_id,
        generation_name=gen.generation_name,
        group_list=group_list_with_pl,
        chart_dates=unique_dates,
        chart_datasets=chart_datasets,
        gen=gen
    )


@trade_bp.route('/generation/<int:generation_id>/download_excel', methods=['GET'])
@login_required
def download_excel(generation_id):
    import io
    import pandas as pd
    from flask import send_file

    # 期の存在チェック
    gen = Generation.query.get_or_404(generation_id)

    # 対象のグループ一覧取得（表示順）
    groups = Group.query.filter_by(generation_id=generation_id).order_by(Group.group_id).all()

    # 各グループごとのPL推移データを取得し、共通の日付リスト（unique_dates）を作成
    group_data_map = {}
    unique_dates = set()
    for g in groups:
        history = get_group_pl_history(generation_id, g.group_id)  # list of (date_str, total_pl)
        history_dict = {date_str: total_pl for date_str, total_pl in history}
        group_data_map[g.group_id] = {
            'group_name': g.group_name,
            'pl_history': history_dict
        }
        for date_str in history_dict.keys():
            unique_dates.add(date_str)

    unique_dates = sorted(list(unique_dates))

    # Excel用にDataFrameを作成
    data = {'日付': unique_dates}
    for g in groups:
        group_id = g.group_id
        group_name = g.group_name
        history = group_data_map[group_id]['pl_history']
        history_keys = sorted(history.keys())
        group_values = []
        for d in unique_dates:
            if d in history:
                group_values.append(history[d])
            else:
                value = 0
                for past_date in reversed(history_keys):
                    if past_date < d:
                        value = history[past_date]
                        break
                group_values.append(value)
        data[group_name] = group_values

    df = pd.DataFrame(data)

    # BytesIOにExcelを書き出す（with文を使用）
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='PL推移')
    output.seek(0)

    filename = f"pl_trend_generation_{generation_id}.xlsx"
    return send_file(
        output,
        download_name=filename,
        as_attachment=True,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )



@trade_bp.route('/generation/<int:generation_id>/groups/edit', methods=['GET', 'POST'])
@login_required
def generation_groups_edit(generation_id):
    """ある期のグループを追加/編集/削除 (admin限定)"""
    if current_user.role != 'admin':
        return "権限がありません", 403
    gen = Generation.query.get_or_404(generation_id)
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'add':
            new_group_name = request.form.get('new_group_name', '').strip()
            if not new_group_name:
                flash('グループ名が空です', 'error')
                return redirect(url_for('trade.generation_groups_edit', generation_id=generation_id))
            new_group = Group(generation_id=generation_id, group_name=new_group_name)
            db.session.add(new_group)
            db.session.commit()
            return redirect(url_for('trade.generation_groups', generation_id=generation_id))
        elif action == 'update':
            group_id = request.form.get('group_id')
            updated_name = request.form.get('updated_name', '').strip()
            group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
            if group_obj and updated_name:
                group_obj.group_name = updated_name
                db.session.commit()
            return redirect(url_for('trade.generation_groups', generation_id=generation_id))
        elif action == 'delete':
            group_id = request.form.get('group_id')
            group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
            if group_obj:
                # 削除前に、対象グループに依存する Accept, Request, PLRecord のレコードを削除
                db.session.query(Accept).filter_by(group_id=group_id).delete(synchronize_session=False)
                db.session.query(Request).filter_by(group_id=group_id).delete(synchronize_session=False)
                db.session.query(PLRecord).filter_by(group_id=group_id).delete(synchronize_session=False)
                db.session.delete(group_obj)
                db.session.commit()
            return redirect(url_for('trade.generation_groups', generation_id=generation_id))
        else:
            flash('不正なアクションです', 'error')
            return redirect(url_for('trade.generation_groups_edit', generation_id=generation_id))
    else:
        groups_in_this_gen = Group.query.filter_by(generation_id=generation_id).all()
        return render_template('groups_edit.html',
                               generation_id=generation_id,
                               generation_name=gen.generation_name,
                               groups_in_this_gen=groups_in_this_gen)


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>')
@login_required
def group_detail(generation_id, group_id):
    """
    グループ詳細表示画面
     - 対象の期・グループ情報を取得
     - 最新の合計PL（グループ全体の最新PL）を算出
     - 全体のPL推移履歴（全銘柄合算）を取得し、Chart.js 用の chart_dates, chart_pl を生成
     - 各銘柄ごとの日別PL履歴（get_group_each_stock_pl_history）を取得
     - 各 ticker について、get_ticker_name_from_api() で銘柄名マッピングを作成
     - 該当グループの Accept テーブル（売買履歴）から取引記録を取得
     - すべてのデータをテンプレートに渡す
    """
    # 対象グループ・期情報の取得
    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")
    gen = Generation.query.get(generation_id)
    
    # 最新合計PLの算出（既存の関数）
    latest_total_pl = get_group_latest_pl(generation_id, group_id)
    
    # 全体のPL推移履歴（全銘柄合算）の取得
    # 例：list of (date_str, total_pl)
    overall_history = get_group_pl_history(generation_id, group_id)
    chart_dates = [d for d, _ in overall_history]
    chart_pl = [pl for _, pl in overall_history]
    
    # 各銘柄ごとのPL履歴を取得（必須）
    group_each_stock_history = get_group_each_stock_pl_history(generation_id, group_id)
    
    # 各 ticker について、銘柄名を取得
    ticker_names = {}
    for ticker in group_each_stock_history.keys():
        ticker_names[ticker] = get_ticker_name_from_api(ticker)
    
    # Acceptテーブルから、該当グループの取引記録（売買履歴）を取得（最新順）
    trade_history = Accept.query.filter_by(generation_id=generation_id, group_id=group_id).order_by(Accept.accepted_date.desc()).all()
    
    return render_template('group_PL.html',
                           generation_id=generation_id,
                           generation_name=gen.generation_name,
                           group=group_obj,
                           latest_total_pl=latest_total_pl,
                           chart_dates=chart_dates,
                           chart_pl=chart_pl,
                           group_each_stock_history=group_each_stock_history,
                           ticker_names=ticker_names,
                           trade_history=trade_history)



# =============================================================================
# 売買申請／承認エンドポイント
# =============================================================================


@trade_bp.route('/trade/request', methods=['GET', 'POST'])
@login_required
def trade_request():
    """
    生徒用 売買申請画面
    GET: request.html を表示（保留中の申請一覧と、所属期内のグループ一覧を渡す）
    POST: フォーム入力で、新規申請または既存申請の更新を行う。
          ※ 同一の generation_id, group_id, ticker, request_type のレコードが存在すれば更新
    """
    # generation_idの取得
    gen_id_str = request.args.get('generation_id') or request.form.get('generation_id') or session.get('current_generation_id')
    if not gen_id_str:
        flash("生成期が指定されていません", "error")
        return redirect(url_for('auth.unified_dashboard'))
    try:
        gen_id = int(gen_id_str)
    except ValueError:
        flash("不正な生成期です", "error")
        return redirect(url_for('auth.unified_dashboard'))
    
    if request.method == 'POST':
        # フォーム値の取得
        ticker_input = request.form.get('ticker')
        trade_side = request.form.get('type')  # "buy" または "sell"
        group_id_str = request.form.get('group_id')
        # 新規項目
        order_method = request.form.get('trade_type')  # "成行" or "指値"
        limit_order_price_str = request.form.get('limit_order_price')
        quantity_str = request.form.get('quantity')
        exec_date_str = request.form.get('requested_execution_date')
        exec_timing = request.form.get('requested_execution_timing')
        requested_date_str = request.form.get('requested_date')
        
        # 必須項目チェック
        if not (ticker_input and trade_side and group_id_str and order_method and quantity_str and exec_date_str and exec_timing and requested_date_str):
            flash("入力不足があります", "error")
            return redirect(url_for('trade.trade_request', generation_id=gen_id))
        
        try:
            group_id = int(group_id_str)
            quantity = float(quantity_str)
            # 執行日は Date 型（※ date() を使って変換）
            exec_date = datetime.strptime(exec_date_str, '%Y-%m-%d').date()
            # 申請日は DateTime 型（フォームからの日付入力の場合、ここは適宜調整）
            requested_date = datetime.strptime(requested_date_str, '%Y-%m-%d')
        except ValueError:
            flash("数値や日付の形式が不正です", "error")
            return redirect(url_for('trade.trade_request', generation_id=gen_id))
        
        ticker = fix_ticker(ticker_input)  # 既存のティッカー整形関数
        
        # 注文方式が「指値」なら、指値価格は必須
        if order_method == "指値":
            if not limit_order_price_str:
                flash("指値注文の場合、指値価格は必須です", "error")
                return redirect(url_for('trade.trade_request', generation_id=gen_id))
            try:
                limit_order_price = float(limit_order_price_str)
            except ValueError:
                flash("指値価格の形式が不正です", "error")
                return redirect(url_for('trade.trade_request', generation_id=gen_id))
        else:
            limit_order_price = None  # 成行の場合は不要
        
        # 管理者の場合、pending の値をフォームから取得（あれば）
        pending_value = None
        if current_user.role == 'admin':
            pending_value_str = request.form.get('pending')
            if pending_value_str is not None:
                try:
                    pending_value = int(pending_value_str)
                except ValueError:
                    flash("承認状態の値が不正です", "error")
                    return redirect(url_for('trade.trade_request', generation_id=gen_id))
        
        # 同一の (generation_id, group_id, ticker, request_type) のレコードがあるかチェック
        existing_request = Request.query.filter_by(
            generation_id=gen_id,
            group_id=group_id,
            ticker=ticker,
            request_type=trade_side
        ).first()
        
        if existing_request:
            # 更新処理
            existing_request.trade_type = order_method
            existing_request.limit_order_price = limit_order_price
            existing_request.request_quantity = quantity
            existing_request.requested_execution_date = exec_date
            existing_request.requested_execution_timing = exec_timing
            existing_request.requested_date = requested_date
            if current_user.role == 'admin' and pending_value is not None:
                existing_request.pending = pending_value
            db.session.commit()
            flash("売買申請を更新しました", "success")
        else:
            # 新規作成
            new_request = Request(
                ticker=ticker,
                generation_id=gen_id,
                group_id=group_id,
                request_type=trade_side,
                trade_type=order_method,
                limit_order_price=limit_order_price,
                request_quantity=quantity,
                requested_execution_date=exec_date,
                requested_execution_timing=exec_timing,
                requested_date=requested_date,
                pending = pending_value if (current_user.role == 'admin' and pending_value is not None) else 0
            )
            db.session.add(new_request)
            db.session.commit()
            flash("売買申請を登録しました", "success")
        return redirect(url_for('trade.trade_request', generation_id=gen_id))
    
    else:
        # GET時：指定された生成期に属する全申請を取得
        pending_requests = Request.query.filter_by(generation_id=gen_id).all()
        group_list = Group.query.filter_by(generation_id=gen_id).all()
        return render_template('request.html', pending_requests=pending_requests, group_list=group_list, generation_id=gen_id)


@trade_bp.route('/trade/request/delete/<int:request_id>', methods=['POST'])
@login_required
def delete_request(request_id):
    """
    指定された request_id の売買申請を削除する。
    POSTメソッドのみ許可。
    """
    # 1. 該当レコードを取得
    req_to_delete = Request.query.get(request_id)
    if not req_to_delete:
        flash("該当の売買申請が見つかりませんでした", "error")
        return redirect(url_for('auth.unified_dashboard'))  # または適切な画面に戻す

    # 2. generation_id をセッションやURLクエリから参照し、整合性を確認しても良い
    #    （例えば、自分がアクセスできる期のリクエストのみ削除できる等）
    #    ここでは簡略化して、常に削除可能とします。
    gen_id_str = request.args.get('generation_id') or session.get('current_generation_id')
    if gen_id_str is None:
        # 万が一、削除後にどこに戻るかが曖昧になるため
        flash("generation_idが不明のため、一覧に戻ります", "error")
        return redirect(url_for('auth.unified_dashboard'))
    
    try:
        gen_id = int(gen_id_str)
    except ValueError:
        flash("不正なgeneration_idです。", "error")
        return redirect(url_for('auth.unified_dashboard'))

    # 3. データベースから削除
    db.session.delete(req_to_delete)
    db.session.commit()

    flash("売買申請を削除しました", "success")

    # 4. 削除後は同じリスト画面(= trade_request)へリダイレクト
    return redirect(url_for('trade.trade_request', generation_id=gen_id))


@trade_bp.route('/trade/accept', methods=['GET', 'POST'])
@login_required
def trade_accept():
    print("=== trade_accept() に入りました ===")
    if current_user.role != 'admin':
        print("管理者権限がありません。")
        abort(403, "アクセス権がありません")

    if request.method == 'POST':
        print("POSTリクエストを受信しました。")
        action = request.form.get('action')
        print(f"フォームからのアクション: {action}")

        if action == 'approve':
            print("【approve】アクションの処理を開始します。")
            req_id = request.form.get('request_id')
            print(f"申請ID: {req_id}")
            trade_req = Request.query.get(req_id)
            if not trade_req:
                flash("申請データが見つかりません", "error")
                print("申請データが見つかりません。")
                return redirect(url_for('trade.trade_accept'))
            try:
                # 管理者が入力した執行情報のみ受け取る
                transaction_price = float(request.form.get('transaction_price'))
                transaction_quantity = float(request.form.get('transaction_quantity'))
                transaction_date_str = request.form.get('transaction_date')
                transaction_date = datetime.strptime(transaction_date_str, '%Y-%m-%d')
                print(f"入力された取引情報: Price={transaction_price}, Quantity={transaction_quantity}, Date={transaction_date}")
            except ValueError:
                print("取引情報の形式が不正です", "error0")
                return redirect(url_for('trade.trade_accept'))

            # Accept レコードは Request の基本情報＋admin の執行入力のみをセット
            approved_trade = Accept(
                ticker=trade_req.ticker,
                generation_id=trade_req.generation_id,
                group_id=trade_req.group_id,
                request_type=trade_req.request_type,
                request_date=trade_req.requested_date,
                transaction_price=transaction_price,
                transaction_quantity=transaction_quantity,
                transaction_date=transaction_date
            )
            db.session.add(approved_trade)
            try:
                print("recalc_pl_from_date() を呼び出します。")
                recalc_pl_from_date(
                    approved_trade.ticker, 
                    approved_trade.generation_id,
                    approved_trade.group_id,
                    approved_trade.transaction_date,
                    approved_trade
                )
            except Exception as e:
                db.session.rollback()
                print(f"承認処理エラー: {str(e)}", "error1")
                return redirect(url_for('trade.trade_accept'))
            db.session.delete(trade_req)
            db.session.commit()
            flash("申請が承認されました", "success")
            print("【approve】処理が正常に終了しました。")
            return redirect(url_for('trade.trade_accept'))

        elif action == 'update':
            print("【update】アクションの処理を開始します。")
            approved_id = request.form.get('approved_id')
            print(f"更新対象の承認済みID: {approved_id}")
            approved_trade = Accept.query.get(approved_id)
            if not approved_trade:
                flash("承認済みデータが見つかりません", "error2")
                print("承認済みデータが見つかりません。")
                return redirect(url_for('trade.trade_accept'))
            try:
                transaction_price = float(request.form.get('transaction_price'))
                transaction_quantity = float(request.form.get('transaction_quantity'))
                transaction_date_str = request.form.get('transaction_date')
                transaction_date = datetime.strptime(transaction_date_str, '%Y-%m-%d')
                print(f"更新後の取引情報: Price={transaction_price}, Quantity={transaction_quantity}, Date={transaction_date}")
            except ValueError:
                flash("取引情報の形式が不正です", "error")
                print("取引情報の形式が不正です。")
                return redirect(url_for('trade.trade_accept'))

            old_transaction_date = approved_trade.transaction_date
            print(f"更新前の取引日: {old_transaction_date}")

            # 更新後の取引日を new_transaction_date として扱う
            new_transaction_date = transaction_date

            # Accept レコードの更新（編集可能なのは執行価格、執行数量、執行日のみ）
            approved_trade.transaction_price = transaction_price
            approved_trade.transaction_quantity = transaction_quantity
            approved_trade.transaction_date = new_transaction_date
            print("更新後の承認レコード:", approved_trade)

            try:
                print("update_pl_from_date() を呼び出します。")
                update_pl_from_date(
                    approved_trade.ticker, 
                    approved_trade.generation_id,
                    approved_trade.group_id,
                    new_transaction_date,
                    old_transaction_date,
                    approved_trade
                )
            except Exception as e:
                db.session.rollback()
                flash(f"更新処理エラー: {str(e)}", "error")
                print(f"更新処理エラー: {str(e)}")
                return redirect(url_for('trade.trade_accept'))
            db.session.commit()
            flash("承認済み申請が更新されました", "success")
            print("【update】処理が正常に終了しました。")
            return redirect(url_for('trade.trade_accept'))

        elif action == 'delete':
            print("【delete】アクションの処理を開始します。")
            approved_id = request.form.get('approved_id')
            print(f"削除対象の承認済みID: {approved_id}")
            approved_trade = Accept.query.get(approved_id)
            if not approved_trade:
                flash("承認済みデータが見つかりません", "error3")
                print("承認済みデータが見つかりません。")
                return redirect(url_for('trade.trade_accept'))
            try:
                print("update_pl_for_deletion() を呼び出します。")
                update_pl_for_deletion(
                    approved_trade.ticker,
                    approved_trade.generation_id,
                    approved_trade.group_id,
                    approved_trade
                )
            except Exception as e:
                db.session.rollback()
                flash(f"削除処理エラー: {str(e)}", "error")
                print(f"削除処理エラー: {str(e)}")
                return redirect(url_for('trade.trade_accept'))
            db.session.delete(approved_trade)
            db.session.commit()
            flash("承認済み申請が削除されました", "success")
            print("【delete】処理が正常に終了しました。")
            return redirect(url_for('trade.trade_accept'))

        else:
            print(f"不明なアクションが指定されました: {action}")
            flash("不正なアクション", "error")
            return redirect(url_for('trade.trade_accept'))

    else:
        print("GETリクエストを受信しました。")
        gen_id_str = request.args.get('generation_id') or request.form.get('generation_id') or session.get('current_generation_id')
        if not gen_id_str:
            print("生成期が指定されていません。")
            return redirect(url_for('auth.unified_dashboard'))
        try:
            gen_id = int(gen_id_str)
            print(f"生成期ID: {gen_id}")
        except ValueError:
            print("不正な生成期です。")

        pending_requests = Request.query.filter_by(generation_id=gen_id, pending=1).all()
        approved_requests = Accept.query.filter_by(generation_id=gen_id).all()
        group_list = Group.query.filter_by(generation_id=gen_id).all()

        print("accept.html をレンダリングします。")
        return render_template(
            'accept.html',
            pending_requests=pending_requests,
            approved_requests=approved_requests,
            group_list=group_list,
        )




# =============================================================================
# ヘルパー関数（PLRecord 更新用、ticker整形、close_price取得等）
# =============================================================================


def get_ticker_name_from_api(ticker: str) -> str:
    """
    指定された ticker に対して、yfinance を用いて銘柄情報を取得し、
    銘柄名（shortName）を "(ticker)" の形式で返す。
    取得に失敗した場合は、ticker をそのまま返す。
    
    Input:
      ticker (str): 例 "1234.T"
    
    Output:
      str: 例 "Toyota Motor Corp (1234.T)"
    """
    try:
        t = yf.Ticker(ticker)
        info = t.info
        name = info.get("shortName")
        if name:
            return f"{name} ({ticker})"
        else:
            return ticker
    except Exception as e:
        print(f"Error fetching ticker name for {ticker}: {e}")
        return ticker


def fix_ticker(ticker_str: str) -> str:
    """
    ticker の整形  
    ・先頭に '^' が付いている場合はそのまま  
    ・それ以外は、末尾に '.T' を付加する（数字＋大文字アルファベットの組み合わせにも対応）
    """
    if not ticker_str.endswith(".T"):
        return ticker_str + ".T"
    return ticker_str


def get_close_price_for_day(ticker, target_date):
    """
    指定日(target_date, datetime.date型)に対する終値を取得する。  
    休日の場合は、対象日に最も近い過去日の値を返す。
    """
    target_date = pd.to_datetime(target_date)
    tokyo_tz = pytz.timezone("Asia/Tokyo")
    dt = pd.to_datetime(target_date.strftime("%Y-%m-%d"))
    dt = tokyo_tz.localize(dt)
    start_date = (dt - timedelta(days=5)).strftime("%Y-%m-%d")
    end_date = (dt + timedelta(days=5)).strftime("%Y-%m-%d")
    # ticker はすでに整形済みとする
    df = yf.download(ticker, start=start_date, end=end_date, progress=False, auto_adjust=False)
    if df.empty:
        raise Exception(f"{ticker} のデータが取得できませんでした。")
    else:
        cp_series = df["Close"]
    cp_series = cp_series.sort_index()
    valid_dates = cp_series.index[cp_series.index <= pd.to_datetime(target_date.strftime("%Y-%m-%d"))]
    if valid_dates.empty:
        raise Exception("対象日以前のデータが存在しません。")
    closest_date = valid_dates[-1]
    return float(cp_series.loc[closest_date].iloc[0])


def get_pl_record(ticker, generation_id, group_id):
    """
    PLRecord を (ticker, generation_id, group_id) で取得する。なければ None を返す。
    """
    return PLRecord.query.filter_by(ticker=ticker, generation_id=generation_id, group_id=group_id).first()


def create_new_pl_record(ticker, generation_id, group_id):
    """
    新しい PLRecord を作成し、初期の pl_data（空の dict）で登録する。
    """
    new_record = PLRecord(
        ticker=ticker,
        generation_id=generation_id,
        group_id=group_id,
        pl_data={}
    )
    db.session.add(new_record)
    db.session.commit()
    return new_record


def update_pl_record(pl_record):
    """
    PLRecord の更新をコミットするためのラッパー
    """
    db.session.add(pl_record)
    db.session.commit()


def get_previous_day_entry(pl_data, target_day_str):
    """
    pl_data は dict（キーは "YYYYMMDD"）  
    target_day_str より前の最新の日付のエントリを返す。なければ None。
    """
    keys = sorted(pl_data.keys())
    prev = None
    for k in keys:
        if k < target_day_str:
            prev = pl_data[k]
        else:
            break
    return prev


def get_group_each_stock_pl_history(generation_id: int, group_id: int) -> dict:
    """
    指定された generation_id, group_id のグループに属する各銘柄の PL 履歴を取得する。
    
    Input:
      generation_id (int): 期のID
      group_id (int): グループのID
      
    Output:
      dict: 銘柄別のPL履歴。例:
            {
                "1234.T": [
                    {"date": "20250117", "pl": 1000},
                    {"date": "20250120", "pl": 1200},
                    ...
                ],
                "5678.T": [
                    {"date": "20250117", "pl": -500},
                    {"date": "20250120", "pl": -300},
                    ...
                ]
            }
    """
    records = PLRecord.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    history = {}
    for record in records:
        ticker = record.ticker
        if not record.pl_data:
            continue
        # pl_data は、各日付(YYYYMMDD)をキーとした dict
        pl_list = []
        for date_str in sorted(record.pl_data.keys()):
            entry = record.pl_data[date_str]
            total_pl = entry.get("holding_pl", 0) + entry.get("sold_pl", 0)
            pl_list.append({"date": date_str, "pl": total_pl})
        history[ticker] = pl_list
    return history

# =============================================================================
# PL 更新処理用関数
# =============================================================================


def recalc_pl_from_date(ticker, generation_id, group_id, start_date, new_approval):
    """
    バックデート再計算：対象日(start_date)から本日までの各営業日に対して、
    初期エントリ（前日のpl_dataまたは初期値）を基に、当日発生の承認取引を適用し、
    当日の終値を用いてPL（holding_pl, sold_pl）を再計算する。

    パラメータ:
      ticker: 銘柄（整形済み）
      generation_id, group_id: 期およびグループのID
      start_date: datetime型、再計算開始日（対象日）
      new_approval: 新たに承認された取引（Acceptオブジェクト、未コミット状態も含む）

    ※ この関数は、get_trading_days(), get_pl_record(), create_new_pl_record(),
        get_accepts(), get_close_price_for_day(), get_previous_day_entry(), update_entry_with_approval(),
        update_pl_record() などの補助関数および FIXED_FEE 定数に依存しています。
    """

    # PLRecordの取得（なければ新規作成）
    pl_record = get_pl_record(ticker, generation_id, group_id)
    if not pl_record:
        pl_record = create_new_pl_record(ticker, generation_id, group_id)
    
    # 本日の日付（Asia/Tokyo）を取得し、対象日から本日までの営業日リストを取得
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    # ※today.strftime('%Y-%m-%d')は使っていないのでそのまま
    print("Today:", today)
    trading_days = get_trading_days(ticker, start_date, today)  # 例: ["20230515", "20230516", ...]
    trading_days.sort()  # 昇順になるようにソート
    print("Trading days:", trading_days)

    # 既存のpl_dataがあれば利用、なければ空の辞書とする
    existing_pl_data = pl_record.pl_data if pl_record.pl_data else {}
    # 対象日（start_date）の文字列
    start_day_str = start_date.strftime("%Y%m%d")
    
    # 初期エントリの取得：対象日直前のエントリがあればそれを、なければ初期値を生成
    init_entry = get_previous_day_entry(existing_pl_data, start_day_str)
    if init_entry is None:
        try:
            cp_init = get_close_price_for_day(ticker, start_date)
        except Exception("株価を取得できません"):
            print("recalc_pl_from_dateにおいて株価取得に失敗しました:", ticker, "and", start_day_str)
            cp_init = None
        init_entry = {
            "close_price": cp_init,
            "holding_quantity": 0,
            "sold_quantity": 0,
            "transaction_price": 0,
            "sold_price": 0,
            "holding_pl": 0,
            "sold_pl": 0
        }

    # 対象日以降の承認取引を取得（新たな承認取引も含む）
    approvals = get_accepts(ticker, generation_id, group_id, start_date)
    if new_approval not in approvals:
        approvals.append(new_approval)
    print("get_accepts() works")
    # 取引日（transaction_date）の昇順にソート
    print(approvals)
    approvals.sort(key=lambda a: a.transaction_date if type(a.transaction_date) is date else a.transaction_date.date())
    print("sorting is working")
    # 承認取引を、取引日ごとにグループ化（キーは "YYYYMMDD" 文字列）
    approvals_by_day = {}
    for appr in approvals:
        day_str = appr.transaction_date.strftime("%Y%m%d")
        approvals_by_day.setdefault(day_str, []).append(appr)
    print("it works here of grouprization")    
    
    # 新たに計算するPLデータ用辞書を初期化し、対象日から本日以降のエントリを計算する
    new_pl_data = {}
    # 対象日より前の既存データは保持する
    for k, v in existing_pl_data.items():
        if k < start_day_str:
            new_pl_data[k] = v
    print("save the older days' records")
    # 対象日を初日のエントリとして初期値を設定
    current_entry = init_entry.copy()
    new_pl_data[start_day_str] = current_entry.copy()
    print("copy and paste!")
    # --- 修正部分：trading_daysと承認取引の日付を統合する ---
    all_days = sorted(set(trading_days).union(set(approvals_by_day.keys())))
    print("All days for calculation:", all_days)
    # 対象日～本日までの各日付についてループ（対象日より前は無視）
    prev_day = start_day_str  # 前日のキーとして初期値
    for day_str in all_days:
        if day_str < start_day_str:
            print("days' comparison works")
            continue  # 対象日より前の日は対象外
        # 初日は既に設定済み。2日目以降は、前日の結果をコピーして開始
        if day_str != start_day_str:
            current_entry = new_pl_data[prev_day].copy()
        # 当日の承認取引がある場合、順次適用
        if day_str in approvals_by_day:
            for appr in approvals_by_day[day_str]:
                try:
                    # update_entry_with_approval は current_entry を更新（in-place更新または返り値として更新）
                    update_entry_with_approval(current_entry, appr, FIXED_FEE)
                except Exception as e:
                    print(f"Error updating entry with approval on {day_str}: {e}")
                    # エラー時はその承認取引をスキップするなど適宜対応
        # 当日の終値を取得して更新
        try:
            dt = datetime.strptime(day_str, "%Y%m%d").date()
            cp = get_close_price_for_day(ticker, dt)
        except Exception as e:
            print(f"Error getting close price for {day_str}: {e}")
            cp = current_entry.get("close_price", None)
        current_entry["close_price"] = cp
        # 再計算：holding_pl = (close_price - transaction_price) * holding_quantity
        current_entry["holding_pl"] = (cp - current_entry["transaction_price"]) * current_entry["holding_quantity"]
        # 当日のエントリを確定として保存
        new_pl_data[day_str] = current_entry.copy()
        prev_day = day_str

    # PLRecord に新たな pl_data を反映して更新（対象日以前のデータも含む）
    pl_record.pl_data = new_pl_data
    update_pl_record(pl_record)
    print("recalc_pl_from_date finished")

def update_pl_from_date(ticker, generation_id, group_id, new_transaction_date, old_transaction_date, updated_approval):
    """
    更新時のバックデート再計算処理（未来方向のトリミング拡張付き）
    Parameters:
      ticker: 対象の銘柄
      generation_id, group_id: 期およびグループのID
      new_transaction_date: 更新後の取引日（datetime型またはdate型）
      old_transaction_date: 更新前の取引日（datetime型またはdate型）
      updated_approval: 更新後の Accept オブジェクト（既に更新済み）
    """
    print("----- update_pl_from_date START -----")
    print(f"Ticker: {ticker}, Generation ID: {generation_id}, Group ID: {group_id}")
    print(f"Old Transaction Date (raw): {old_transaction_date}, New Transaction Date (raw): {new_transaction_date}")

    # 日付型の統一（両方とも date 型に変換）
    if isinstance(new_transaction_date, datetime):
        new_transaction_date = new_transaction_date.date()
    if isinstance(old_transaction_date, datetime):
        old_transaction_date = old_transaction_date.date()
    print(f"New Transaction Date (as date): {new_transaction_date}, Old Transaction Date (as date): {old_transaction_date}")

    # 基本の再計算開始日は、min(old, new)
    base_start_date = min(new_transaction_date, old_transaction_date)
    print(f"Base start_date (min(old, new)): {base_start_date}")

    # 新しい取引日より前に Accept レコードが存在するかをチェック
    earlier_accepts = Accept.query.filter(
        Accept.ticker == ticker,
        Accept.generation_id == generation_id,
        Accept.group_id == group_id,
        func.date(Accept.transaction_date) < new_transaction_date
    ).all()
    
    if not earlier_accepts:
        print("No Accept records exist before new transaction date.")
        # 履歴は不要なので、start_date を新しい取引日にする
        start_date = new_transaction_date
        preserve_history = False
        # 既存のPLRecordデータは無視する（空にする）
        existing_pl_data = {}
    else:
        preserve_history = True
        # 既存のPLRecordデータはそのまま使う
        existing_pl_data = pl_record.pl_data if (pl_record := get_pl_record(ticker, generation_id, group_id)) and pl_record.pl_data else {}

    print(f"Calculated start_date for recalculation: {start_date}")

    # PLRecordの取得（なければ新規作成）
    pl_record = get_pl_record(ticker, generation_id, group_id)
    if not pl_record:
        pl_record = create_new_pl_record(ticker, generation_id, group_id)
    print("PLRecord obtained.")

    # 本日の日付（Asia/Tokyo）と、start_date～本日までの営業日リストを取得
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    trading_days = get_trading_days(ticker, start_date, today)
    trading_days.sort()
    print(f"Trading days from {start_date} to {today}: {trading_days}")

    # start_day_str は再計算開始日の文字列（YYYYMMDD）
    start_day_str = start_date.strftime("%Y%m%d")
    print(f"Start day string: {start_day_str}")

    # preserved_data：履歴を保持する場合のみ、start_date より前のデータを保存。なければ空
    if preserve_history:
        preserved_data = {k: v for k, v in existing_pl_data.items() if k < start_day_str}
    else:
        preserved_data = {}
        print("Historical PLRecord data will be discarded.")

    # 初期エントリの取得（start_date直前のエントリがあれば利用；なければ新規生成）
    init_entry = get_previous_day_entry(existing_pl_data, start_day_str)
    if init_entry is None:
        try:
            cp_init = get_close_price_for_day(ticker, start_date)
        except Exception:
            print("update_pl_from_date: Failed to get close price for:", ticker, start_day_str)
            cp_init = None
        # ここでは履歴がない場合はゼロから開始する
        init_entry = {
            "close_price": cp_init,
            "holding_quantity": 0,
            "sold_quantity": 0,
            "transaction_price": 0,
            "sold_price": 0,
            "holding_pl": 0,
            "sold_pl": 0
        }
    print("Initial entry:", init_entry)

    # 対象日以降の承認取引（Acceptレコード）を取得
    approvals = get_accepts(ticker, generation_id, group_id, start_date)
    print(f"Approvals retrieved: {len(approvals)} records before adding updated approval.")
    if updated_approval not in approvals:
        approvals.append(updated_approval)
        print("Updated approval added to approvals list.")

    # 承認取引を取引日順にソート（すべて date 型で比較）
    approvals.sort(key=lambda a: a.transaction_date.date() if isinstance(a.transaction_date, datetime) else a.transaction_date)
    print("Approvals after sorting:")
    for appr in approvals:
        print(f"  - {appr.transaction_date} (Ticker: {appr.ticker}, Type: {appr.request_type})")

    # 承認取引を日付ごとにグループ化（キーはYYYYMMDD）
    approvals_by_day = {}
    for appr in approvals:
        day_str = appr.transaction_date.strftime("%Y%m%d")
        approvals_by_day.setdefault(day_str, []).append(appr)
    print("Approvals grouped by day:")
    for day, lst in approvals_by_day.items():
        print(f"  {day}: {len(lst)} approvals")

    # 新たなPLRecordデータの再計算
    new_data = {}
    # 初期エントリ（ベースはゼロから再構築する）
    current_entry = init_entry.copy()
    new_data[start_day_str] = current_entry.copy()

    # 営業日と承認取引日を統合した全日リスト
    all_days = sorted(set(trading_days).union(set(approvals_by_day.keys())))
    print("All calculation days:", all_days)
    
    prev_day = start_day_str
    for day_str in all_days:
        if day_str < start_day_str:
            continue
        if day_str != start_day_str:
            current_entry = new_data[prev_day].copy()
        if day_str in approvals_by_day:
            for appr in approvals_by_day[day_str]:
                try:
                    update_entry_with_approval(current_entry, appr, FIXED_FEE)
                    print(f"Applied approval on {day_str}: {appr}")
                except Exception as e:
                    print(f"Error updating entry with approval on {day_str}: {e}")
        try:
            dt = datetime.strptime(day_str, "%Y%m%d").date()
            cp = get_close_price_for_day(ticker, dt)
        except Exception as e:
            print(f"Error getting close price for {day_str}: {e}")
            cp = current_entry.get("close_price", None)
        current_entry["close_price"] = cp
        current_entry["holding_pl"] = (cp - current_entry["transaction_price"]) * current_entry["holding_quantity"]
        new_data[day_str] = current_entry.copy()
        prev_day = day_str
        print(f"Updated PL entry for {day_str}: {current_entry}")

    # ★ 未来方向のトリミング処理 ★
    if approvals:
        max_accept_date = max(
            (appr.transaction_date.date() if isinstance(appr.transaction_date, datetime) else appr.transaction_date)
            for appr in approvals
        )
        print(f"Computed max_accept_date: {max_accept_date} (type: {type(max_accept_date)})")
        max_accept_day_str = max_accept_date.strftime("%Y%m%d")
        print(f"max_accept_day_str: {max_accept_day_str} (type: {type(max_accept_day_str)})")
        
        print("new_data keys and their types:")
        for k in new_data.keys():
            print(f"  key: {k} (type: {type(k)})")
        
        if max_accept_day_str in new_data:
            holding_qty = new_data[max_accept_day_str].get("holding_quantity", None)
            print(f"new_data[{max_accept_day_str}]['holding_quantity']: {holding_qty} (type: {type(holding_qty)})")
        else:
            print(f"{max_accept_day_str} is not a key in new_data.")
        
        # トリミング処理：最新Acceptの日付以降のエントリを削除
        if max_accept_day_str in new_data and new_data[max_accept_day_str].get("holding_quantity", None) == 0:
            print(f"Trimming PLRecord entries with date > {max_accept_day_str} because holding_quantity is 0.")
            trimmed_data = {}
            for k, v in new_data.items():
                if int(k) <= int(max_accept_day_str):
                    trimmed_data[k] = v
                    print(f"Kept key: {k}")
                else:
                    print(f"Trimmed key: {k}")
            new_data = trimmed_data
    # ★ 未来方向のトリミング処理 ここまで ★

    # 最終的に、preserved_data と new_data をマージして更新
    final_pl_data = {}
    final_pl_data.update(preserved_data)
    final_pl_data.update(new_data)
    pl_record.pl_data = final_pl_data
    update_pl_record(pl_record)
    print("update_pl_from_date finished")
    print("----- update_pl_from_date END -----")



def get_trading_days(ticker, start_date, end_date):
    """
    yfinance を利用して、対象期間内の営業日（日付文字列 "YYYYMMDD" のリスト）を取得する。
    """
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date) + pd.Timedelta(days=1) #end_dateを含めるため

    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    df = yf.download(ticker, start=start_str, end=end_str, progress=False, auto_adjust=False)

    if df.empty:
        raise Exception(f"{ticker} のデータが取得できませんでした。")

    # ** df.index を明示的に DatetimeIndex に変換 **
    df.index = pd.to_datetime(df.index)  # 念のため再変換
    df.index = df.index.to_pydatetime()  # Pythonのdatetime型に変換

    # ** ループ時に pd.to_datetime を適用 **
    try:
        trading_days = [d.strftime("%Y%m%d") for d in df.index]
    except Exception as e:
        print("🚨 エラー発生:", e)
        print("df.index の内容:", df.index)
        raise e  # 例外を再送

    return trading_days

def update_pl_for_deletion(ticker, generation_id, group_id, deleted_approval):
    """
    削除時のバックデート再計算処理
    Parameters:
      ticker: 対象の銘柄（整形済み）
      generation_id, group_id: 期およびグループのID
      deleted_approval: 削除対象の Accept オブジェクト
    """
    print("----- update_pl_for_deletion START -----")
    
    # まず、該当のticker, generation, groupに対して全Acceptレコードを取得し、
    # 削除対象を除いた残りがないかチェックする
    all_accepts = Accept.query.filter_by(ticker=ticker, generation_id=generation_id, group_id=group_id).all()
    remaining_accepts = [acc for acc in all_accepts if acc.accept_id != deleted_approval.accept_id]
    if not remaining_accepts:
        print(f"No remaining approvals for ticker {ticker}, generation {generation_id}, group {group_id}. Deleting PLRecord.")
        pl_record = get_pl_record(ticker, generation_id, group_id)
        if pl_record:
            db.session.delete(pl_record)
        print("update_pl_for_deletion finished (PLRecord deleted)")
        return

    # 削除対象の取引日を再計算開始日とする（datetime型の場合はdateに変換）
    start_date = (deleted_approval.transaction_date
                  if isinstance(deleted_approval.transaction_date, date)
                  else deleted_approval.transaction_date.date())
    print(f"Start date for deletion recalculation: {start_date}")

    # PLRecordの取得（なければ新規作成）
    pl_record = get_pl_record(ticker, generation_id, group_id)
    if not pl_record:
        pl_record = create_new_pl_record(ticker, generation_id, group_id)

    # 本日の日付（Asia/Tokyo）を取得し、start_dateから本日までの営業日リストを取得
    today = datetime.now(ZoneInfo("Asia/Tokyo")).date()
    trading_days = get_trading_days(ticker, start_date, today)
    trading_days.sort()
    print(f"Trading days from {start_date} to {today}: {trading_days}")

    # 既存の pl_data（なければ空の辞書）を取得し、start_date文字列を作成
    existing_pl_data = pl_record.pl_data if pl_record.pl_data else {}
    start_day_str = start_date.strftime("%Y%m%d")

    # 初期エントリの取得：start_date直前のエントリがあればそれを、なければ初期値を生成
    init_entry = get_previous_day_entry(existing_pl_data, start_day_str)
    if init_entry is None:
        try:
            cp_init = get_close_price_for_day(ticker, start_date)
        except Exception as e:
            print(f"update_pl_for_deletion: 株価取得に失敗しました: {ticker}, {start_day_str}")
            cp_init = None
        init_entry = {
            "close_price": cp_init,
            "holding_quantity": 0,
            "sold_quantity": 0,
            "transaction_price": 0,
            "sold_price": 0,
            "holding_pl": 0,
            "sold_pl": 0
        }
    print("Initial entry for deletion recalculation:", init_entry)

    # start_date以降の承認取引を取得し、削除対象の取引は除外する
    approvals = get_accepts(ticker, generation_id, group_id, start_date)
    approvals = [appr for appr in approvals if appr.accept_id != deleted_approval.accept_id]
    print(f"Number of approvals after filtering deletion: {len(approvals)}")

    # 承認取引を取引日順にソートし、日毎にグループ化する
    approvals.sort(key=lambda a: a.transaction_date if isinstance(a.transaction_date, date)
                                  else a.transaction_date.date())
    approvals_by_day = {}
    for appr in approvals:
        day_str = appr.transaction_date.strftime("%Y%m%d")
        approvals_by_day.setdefault(day_str, []).append(appr)
    print("Approvals grouped by day (after deletion filtering):", approvals_by_day)

    # 既存のPLデータのうち、start_dateより前のデータは保持し、以降は再計算する
    new_pl_data = {}
    for k, v in existing_pl_data.items():
        if k < start_day_str:
            new_pl_data[k] = v
    current_entry = init_entry.copy()
    new_pl_data[start_day_str] = current_entry.copy()

    # 営業日と承認取引があった日を統合した全日リストを作成
    all_days = sorted(set(trading_days).union(set(approvals_by_day.keys())))
    print("All days for deletion recalculation:", all_days)
    prev_day = start_day_str
    for day_str in all_days:
        if day_str < start_day_str:
            continue
        if day_str != start_day_str:
            current_entry = new_pl_data[prev_day].copy()
        if day_str in approvals_by_day:
            for appr in approvals_by_day[day_str]:
                try:
                    update_entry_with_approval(current_entry, appr, FIXED_FEE)
                except Exception as e:
                    print(f"Error updating entry with approval on {day_str}: {e}")
        try:
            dt = datetime.strptime(day_str, "%Y%m%d").date()
            cp = get_close_price_for_day(ticker, dt)
        except Exception as e:
            print(f"Error getting close price for {day_str}: {e}")
            cp = current_entry.get("close_price", None)
        current_entry["close_price"] = cp
        current_entry["holding_pl"] = (cp - current_entry["transaction_price"]) * current_entry["holding_quantity"]
        new_pl_data[day_str] = current_entry.copy()
        prev_day = day_str

    # 新たなPLデータをPLRecordに反映して更新
    pl_record.pl_data = new_pl_data
    update_pl_record(pl_record)
    print("update_pl_for_deletion finished")
    print("----- update_pl_for_deletion END -----")

def update_entry_with_approval(entry, approval, fee):
    """
    補助関数：1件の承認取引を既存エントリに適用する。  
    """
    cp = get_close_price_for_day(approval.ticker, approval.transaction_date)
    entry["close_price"] = cp
    if approval.request_type.lower() == "buy":
        if entry["holding_quantity"] == 0:
            entry["holding_quantity"] = approval.transaction_quantity
            entry["transaction_price"] = approval.transaction_price
            entry["sold_pl"] = -fee
        elif entry["holding_quantity"] > 0:
            old_qty = entry["holding_quantity"]
            old_price = entry["transaction_price"]
            new_qty = old_qty + approval.transaction_quantity
            new_avg = (old_qty * old_price + approval.transaction_quantity * approval.transaction_price) / new_qty
            entry["holding_quantity"] = new_qty
            entry["transaction_price"] = new_avg
            entry["sold_pl"] += -fee
        else:
            raise Exception("保有数量がマイナスです")
        entry["holding_pl"] = (cp - entry["transaction_price"]) * entry["holding_quantity"]
    elif approval.request_type.lower() == "sell":
        if approval.transaction_quantity > entry["holding_quantity"]:
            raise Exception("売却数量が保有数量を超えています")
        old_holding = entry["holding_quantity"]
        old_sold_qty = entry["sold_quantity"]
        old_sold_price = entry["sold_price"]
        new_holding = old_holding - approval.transaction_quantity
        new_sold_qty = old_sold_qty + approval.transaction_quantity
        entry["holding_quantity"] = new_holding
        entry["sold_quantity"] = new_sold_qty
        new_sold_price = (old_sold_qty * old_sold_price + approval.transaction_quantity * approval.transaction_price) / new_sold_qty
        entry["sold_price"] = new_sold_price
        entry["sold_pl"] += (approval.transaction_price - entry["transaction_price"]) * approval.transaction_quantity - fee
        entry["holding_pl"] = (cp - entry["transaction_price"]) * new_holding
    else:
        raise Exception("不正な承認タイプ")
    return entry


def get_accepts(ticker, generation_id, group_id, start_date):
    """
    補助関数：Accept テーブルから、指定された (ticker, generation_id, group_id)
    かつ transaction_date >= start_date の承認取引を昇順に取得する。
    """
    # start_dateがdatetime型ならdate型に変換
    if isinstance(start_date, datetime):
        start_date = start_date.date()
    print("it is ok")
    return Accept.query.filter(
        Accept.ticker == ticker,
        Accept.generation_id == generation_id,
        Accept.group_id == group_id,
        Accept.transaction_date >= start_date
    ).order_by(Accept.transaction_date.asc()).all()

# =============================================================================
# グループ毎の PL 履歴取得用ヘルパー関数
# =============================================================================


def get_group_pl_history(generation_id, group_id):
    """
    指定されたグループ内の全銘柄（すべての PLRecord）から、各日付の holding_pl + sold_pl を合算し、
    (date_str, total_pl) のリストとして返す。
    """
    records = PLRecord.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    history = {}
    for rec in records:
        if not rec.pl_data:
            continue
        for date_str, data in rec.pl_data.items():
            total = data.get("holding_pl", 0) + data.get("sold_pl", 0)
            history[date_str] = history.get(date_str, 0) + total
    # ソートしてリスト化
    history_list = sorted(history.items())
    return history_list


def get_group_latest_pl(generation_id, group_id):
    """
    指定されたグループ内の全銘柄の最新 pl_data の（holding_pl + sold_pl）の合計を返す。
    """
    records = PLRecord.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    total = 0
    for rec in records:
        if rec.pl_data:
            latest_date = max(rec.pl_data.keys())
            data = rec.pl_data[latest_date]
            total += data.get("holding_pl", 0) + data.get("sold_pl", 0)
    return total


def get_previous_day_entry(pl_data, today_str):
    """
    pl_data の中から、today_str より前の最大の日付キーに対応するエントリを返す。
    """
    valid_keys = [k for k in pl_data.keys() if k < today_str]
    if not valid_keys:
        return None
    prev_key = max(valid_keys)
    return pl_data[prev_key]


from sqlalchemy.orm.attributes import flag_modified

@trade_bp.cli.command('update_pl')
def update_pl():
    import logging
    import pytz
    from datetime import datetime
    logging.info("update_pl: コマンド開始")
    
    # 東京時間で当日の日付を取得
    try:
        tokyo_tz = pytz.timezone("Asia/Tokyo")
        today = datetime.now(tokyo_tz).date()
        today_str = today.strftime("%Y%m%d")
        logging.info(f"update_pl: 当日の日付取得成功 - {today_str}")
    except Exception as e:
        logging.error(f"update_pl: 当日の日付取得失敗: {str(e)}")
        return

    # PLRecord のクエリ
    logging.info("update_pl: PLRecordのクエリ開始")
    try:
        pl_records = (
            PLRecord.query
            .join(Generation, PLRecord.generation_id == Generation.generation_id)
            .filter(Generation.activeness == 1)
            .all()
        )
    except Exception as e:
        logging.error(f"update_pl: PLRecordクエリ失敗: {str(e)}")
        return

    logging.info(f"update_pl: クエリ完了 - 更新対象のPLRecord数: {len(pl_records)}")
    logging.info("update_pl: PLRecordの処理開始")
    for record in pl_records:
        try:
            logging.info(f"update_pl: レコード開始 - ID: {record.pl_record_id}, Ticker: {record.ticker}")
            ticker = fix_ticker(record.ticker)
            generation_id = record.generation_id
            # 新しい辞書オブジェクトとして pl_data を作成（既存内容はコピー）
            current_pl_data = dict(record.pl_data) if record.pl_data else {}
    
            if today_str not in current_pl_data:
                prev_entry = get_previous_day_entry(current_pl_data, today_str)
                # 新規エントリは、前日のエントリ（コピー）または空の初期値を設定
                new_entry = prev_entry.copy() if prev_entry else {}
                current_pl_data[today_str] = new_entry
                logging.info(f"update_pl: {ticker} (Gen {generation_id}): 当日エントリ無し - 前日コピー作成")
                
            entry = current_pl_data[today_str]
            old_close = entry.get("close_price", None)
            new_close = get_close_price_for_day(ticker, today)
            entry["close_price"] = new_close
            transaction_price = entry.get("transaction_price", 0)
            holding_quantity = entry.get("holding_quantity", 0)
            new_holding_pl = (new_close - transaction_price) * holding_quantity
            entry["holding_pl"] = new_holding_pl

            # 完全なコピーにより、更新内容が新しいオブジェクトとして再代入される
            current_pl_data[today_str] = entry.copy()
            # record.pl_data も新しい dict として再代入
            record.pl_data = current_pl_data.copy()
            # 明示的に変更をフラグ
            flag_modified(record, "pl_data")
            db.session.add(record)
    
            logging.info(f"update_pl: {ticker} (Gen {generation_id}): old_close={old_close}, new_close={new_close}")
            logging.info(f"update_pl: {ticker}: transaction_price={transaction_price}, holding_quantity={holding_quantity}, new holding_pl={new_holding_pl}")
        except Exception as e:
            logging.error(f"update_pl: レコード処理中エラー (Ticker {record.ticker}): {str(e)}")
            continue

    try:
        db.session.commit()
        logging.info("update_pl: 全てのPLRecord更新完了")
    except Exception as e:
        db.session.rollback()
        logging.error(f"update_pl: DBコミットエラー: {str(e)}")

