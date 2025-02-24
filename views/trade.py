# views/trade.py

from flask import Blueprint, render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user
from models import db
from models.user_models import Users
from models.trade_models import Generation, Group, Holding, Sold, PLHistory
from datetime import datetime, timezone
import yfinance as yf
import pandas as pd
import logging

trade_bp = Blueprint('trade', __name__)

logging.basicConfig(level=logging.INFO)
FIXED_COST = 500.0  # 固定手数料


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
                # 該当期のユーザーも削除
                Users.query.filter_by(generation_id=gen_id).delete()

                # 関連データ削除
                PLHistory.query.filter_by(generation_id=gen_id).delete()
                Holding.query.filter_by(generation_id=gen_id).delete()
                Sold.query.filter_by(generation_id=gen_id).delete()
                Group.query.filter_by(generation_id=gen_id).delete()

                db.session.delete(gen)
                db.session.commit()
            return redirect(url_for('auth.unified_dashboard'))

        # --- 変更箇所: activeness の更新処理を追加 ---
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


@trade_bp.route('/generation/<int:generation_id>/groups', methods=['GET'])
@login_required
def generation_groups(generation_id):
    """
    ある期(generation_id)のグループ一覧 + 全グループ分のPL推移グラフ
    """
    gen = Generation.query.get_or_404(generation_id)
    groups = Group.query.filter_by(generation_id=generation_id).order_by(Group.group_id).all()

    # 各グループの最新PL
    group_list_with_pl = []
    for g in groups:
        latest_pl_hist = PLHistory.query.filter_by(
            generation_id=generation_id,
            group_id=g.group_id
        ).order_by(PLHistory.date.desc()).first()

        total_pl = latest_pl_hist.total_pl if latest_pl_hist else 0.0
        group_list_with_pl.append({
            'group_id': g.group_id,
            'group_name': g.group_name,
            'total_pl': total_pl
        })

    # グラフ用データ
    all_hist = PLHistory.query.filter_by(generation_id=generation_id).order_by(PLHistory.date).all()
    unique_dates = sorted(list(set(str(h.date) for h in all_hist)))

    group_data_map = {}
    for g in groups:
        group_data_map[g.group_id] = {
            'name': g.group_name,
            'pl_by_date': {}
        }

    for rec in all_hist:
        date_str = str(rec.date)
        if rec.group_id in group_data_map:
            group_data_map[rec.group_id]['pl_by_date'][date_str] = rec.total_pl

    import random
    chart_datasets = []
    for g_obj in groups:
        g_id = g_obj.group_id
        info = group_data_map[g_id]
        pl_values = []
        for d in unique_dates:
            pl_values.append(info['pl_by_date'].get(d, None))

        r = random.randint(50, 200)
        g_ = random.randint(50, 200)
        b = random.randint(50, 200)
        color_str = f"rgba({r},{g_},{b},1)"

        chart_datasets.append({
            'label': info['name'],
            'data': pl_values,
            'borderColor': color_str,
            'fill': False
        })

    return render_template(
        'groups_list.html',
        generation_id=generation_id,
        generation_name=gen.generation_name,
        group_list=group_list_with_pl,
        chart_dates=unique_dates,
        chart_datasets=chart_datasets
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
                Holding.query.filter_by(generation_id=generation_id, group_id=group_id).delete()
                Sold.query.filter_by(generation_id=generation_id, group_id=group_id).delete()
                PLHistory.query.filter_by(generation_id=generation_id, group_id=group_id).delete()

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
    グループ詳細(保有銘柄一覧, 売却履歴, PL推移など)
    """

    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")

    latest_pl = PLHistory.query.filter_by(
        generation_id=generation_id,
        group_id=group_id
    ).order_by(PLHistory.pl_history_id.desc()).first()
    latest_total_pl = latest_pl.total_pl if latest_pl else 0.0

    holdings = Holding.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    solds = Sold.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    pl_history = PLHistory.query.filter_by(generation_id=generation_id, group_id=group_id)\
                                .order_by(PLHistory.date).all()

    chart_dates = [rec.date.strftime('%Y-%m-%d') for rec in pl_history]
    chart_pl = [rec.total_pl for rec in pl_history]

    return render_template('group_PL.html',
                           generation_id=generation_id,
                           group=group_obj,
                           latest_total_pl=latest_total_pl,
                           holdings=holdings,
                           solds=solds,
                           pl_history=pl_history,
                           chart_dates=chart_dates,
                           chart_pl=chart_pl)


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/bs_input', methods=['GET', 'POST'])
@login_required
def bs_input(generation_id, group_id):
    """
    買い/売り入力ページ
    adminか、あるいはuserであれば同じgeneration_idの場合にのみ許可
    """
    if current_user.role != 'admin':
        if current_user.generation_id != generation_id:
            return "アクセス権がありません", 403

    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")

    if request.method == 'POST':
        trade_date_str = request.form.get('trade_date')
        ticker_input = request.form.get('ticker')
        action = request.form.get('action')
        quantity_str = request.form.get('quantity')
        price_str = request.form.get('price')

        if not (trade_date_str and ticker_input and action and quantity_str and price_str):
            flash('入力不足があります', 'error')
            return redirect(url_for('trade.bs_input', generation_id=generation_id, group_id=group_id))

        try:
            quantity = float(quantity_str)
            price = float(price_str)
        except ValueError:
            flash('数量や価格は数値を入力してください', 'error')
            return redirect(url_for('trade.bs_input', generation_id=generation_id, group_id=group_id))

        try:
            trade_date = datetime.strptime(trade_date_str, '%Y-%m-%d')
        except ValueError:
            trade_date = datetime.now(timezone.utc)

        ticker = fix_jp_ticker(ticker_input)

        if action.lower() == 'buy':
            buy_handler(generation_id, group_id, trade_date, ticker, quantity, price)
        elif action.lower() == 'sell':
            sell_handler(generation_id, group_id, trade_date, ticker, quantity, price)
        else:
            flash('アクションが不正です', 'error')
            return redirect(url_for('trade.bs_input', generation_id=generation_id, group_id=group_id))

        flash('取引を登録しました', 'success')
        return redirect(url_for('trade.group_detail', generation_id=generation_id, group_id=group_id))
    else:
        return render_template('group_BS_input.html', group=group_obj, generation_id=generation_id)


def fix_jp_ticker(ticker_str: str) -> str:
    """数字4桁の場合、末尾に'.T'を付ける補助関数"""
    if ticker_str.endswith('.T'):
        return ticker_str
    if ticker_str.isdigit():
        return ticker_str + '.T'
    return ticker_str


def buy_handler(generation_id, group_id, trade_date, ticker, quantity, price):
    """買い処理"""
    holding = Holding.query.filter_by(
        generation_id=generation_id,
        group_id=group_id,
        ticker=ticker
    ).first()

    if holding:
        old_qty = holding.quantity
        old_price = holding.buy_price
        new_qty = old_qty + quantity
        new_buy_price = ((old_price * old_qty) + (price * quantity)) / new_qty

        holding.trade_date = trade_date
        holding.quantity = new_qty
        holding.buy_price = new_buy_price
        holding.current_price = price
        holding.current_pl = (price - new_buy_price) * new_qty - FIXED_COST
    else:
        ticker_name = "Unknown"
        try:
            ticker_name = get_ticker_name_from_api(ticker)
        except:
            pass

        new_holding = Holding(
            generation_id=generation_id,
            group_id=group_id,
            trade_date=trade_date,
            ticker=ticker,
            ticker_name=ticker_name,
            quantity=quantity,
            buy_price=price,
            current_price=price,
            current_pl=(price - price)*quantity - FIXED_COST
        )
        db.session.add(new_holding)
    db.session.commit()


def sell_handler(generation_id, group_id, trade_date, ticker, sell_quantity, sell_price):
    """売り処理"""
    holding = Holding.query.filter_by(
        generation_id=generation_id,
        group_id=group_id,
        ticker=ticker
    ).first()

    if not holding:
        flash('保有していない銘柄は売却できません', 'error')
        return

    if sell_quantity > holding.quantity:
        flash('売却数量が保有数量を超えています', 'error')
        return

    old_buy_price = holding.buy_price
    old_quantity = holding.quantity
    realized_pl = (sell_price - old_buy_price) * sell_quantity - FIXED_COST

    sold_record = Sold(
        generation_id=generation_id,
        group_id=group_id,
        trade_date=trade_date,
        ticker=ticker,
        ticker_name=holding.ticker_name,
        sold_quantity=sell_quantity,
        buy_price=old_buy_price,
        sell_price=sell_price,
        realized_pl=realized_pl
    )
    db.session.add(sold_record)

    new_qty = old_quantity - sell_quantity
    if new_qty <= 0:
        db.session.delete(holding)
    else:
        holding.quantity = new_qty
        holding.current_price = sell_price
        holding.current_pl = (sell_price - holding.buy_price) * new_qty - FIXED_COST

    db.session.commit()


def get_ticker_name_from_api(ticker: str) -> str:
    """yfinance APIから銘柄名を取得する例"""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if 'longName' in info and info['longName']:
            return info['longName']
        elif 'shortName' in info and info['shortName']:
            return info['shortName']
    except:
        pass
    return "Unknown_" + ticker


# --- CLIコマンドの例 ---
@trade_bp.cli.command('update_prices')
def update_prices():
    """株価更新"""
    # --- 変更箇所: activeな期(Generation.activeness==1) のみ対象にする ---
    holdings = (Holding.query
                .join(Generation, Holding.generation_id == Generation.generation_id)
                .filter(Generation.activeness == 1)
                .all())
    for h in holdings:
        try:
            data = yf.download(h.ticker, period="1d", interval="1h")
            if not data.empty:
                latest_close = data.iloc[-1]['Close']
                if isinstance(latest_close, pd.Series):
                    latest_close = float(latest_close.iloc[0])
                h.current_price = float(latest_close)
                h.current_pl = (h.current_price - h.buy_price) * h.quantity - FIXED_COST
            else:
                h.current_price = 0.0
                h.current_pl = - FIXED_COST
        except Exception as e:
            logging.error(f"Error updating price for {h.ticker}: {e}")
            h.current_price = 0.0
            h.current_pl = - FIXED_COST
    db.session.commit()
    logging.info("update_prices: 株価を更新しました。")


@trade_bp.cli.command('update_total_pl')
def update_total_pl():
    """日次で全グループの合計PLをPLHistoryに記録"""
    # --- 変更箇所: activeな期のみグループを取得する ---
    groups = (Group.query
              .join(Generation, Group.generation_id == Generation.generation_id)
              .filter(Generation.activeness == 1)
              .all())
    today = datetime.now(timezone.utc).date()
    for g in groups:
        # 含み損益
        holdings = Holding.query.filter_by(generation_id=g.generation_id, group_id=g.group_id).all()
        total_unrealized = sum(h.current_pl for h in holdings)

        # 実現損益
        solds = Sold.query.filter_by(generation_id=g.generation_id, group_id=g.group_id).all()
        total_realized = sum(s.realized_pl for s in solds)

        total_pl_value = total_unrealized + total_realized

        existing = PLHistory.query.filter_by(
            generation_id=g.generation_id,
            group_id=g.group_id,
            date=today
        ).first()
        if existing:
            existing.total_pl = total_pl_value
        else:
            new_pl = PLHistory(
                generation_id=g.generation_id,
                group_id=g.group_id,
                date=today,
                total_pl=total_pl_value
            )
            db.session.add(new_pl)

    db.session.commit()
    logging.info("update_total_pl: 本日の total_pl を更新しました。")


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/bs_edit', methods=['GET'])
@login_required
def group_bs_edit(generation_id, group_id):
    """
    保有銘柄と売却済み銘柄を編集するページ
    """
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")

    gen = Generation.query.get_or_404(generation_id)

    holdings = Holding.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    solds = Sold.query.filter_by(generation_id=generation_id, group_id=group_id).all()

    pl_history_list = PLHistory.query.filter_by(generation_id=generation_id, group_id=group_id)\
                                     .order_by(PLHistory.date).all()
    
    
    return render_template('group_BS_edit.html',
                           generation_id=generation_id,
                           generation_name=gen.generation_name,
                           group=group_obj,
                           holdings=holdings,
                           solds=solds,
                           pl_history_list=pl_history_list)


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/plhistory/new', methods=['POST'])
@login_required
def new_plhistory(generation_id, group_id):
    # 1) 権限チェック
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    # 2) POSTフォームから値を取得
    new_date_str = request.form.get('new_date')
    new_pl_str = request.form.get('new_total_pl')
    if not new_date_str or not new_pl_str:
        flash("日付やPLが未入力です", "error")
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # 3) 未来日チェック + 数値変換
    try:
        dt = datetime.strptime(new_date_str, '%Y-%m-%d').date()
        if dt > datetime.now().date():
            flash("未来の日付は登録できません", "error")
            return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

        val_pl = float(new_pl_str)
    except ValueError:
        flash("日付または数値が不正です", "error")
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # 4) もし同じ日付が既にあるなら「上書き」する (oldを塗り替える)
    existing_rec = PLHistory.query.filter_by(
        generation_id=generation_id,
        group_id=group_id,
        date=dt
    ).first()
    if existing_rec:
        existing_rec.total_pl = val_pl
        db.session.commit()
        flash("同じ日付があったため上書きしました", "success")
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # 5) 新規追加
    new_rec = PLHistory(
        generation_id=generation_id,
        group_id=group_id,
        date=dt,
        total_pl=val_pl
    )
    db.session.add(new_rec)
    db.session.commit()

    flash("PL履歴を追加しました", "success")
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/plhistory/<int:pl_id>/update', methods=['POST'])
@login_required
def update_plhistory(generation_id, group_id, pl_id):
    # 権限チェック
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    # 該当PLHistoryを取得
    pl_rec = PLHistory.query.filter_by(
        generation_id=generation_id,
        group_id=group_id,
        pl_history_id=pl_id
    ).first()
    if not pl_rec:
        abort(404, "該当するPL履歴が見つかりません")

    # フォーム入力
    new_date_str = request.form.get('new_date')
    new_pl_str = request.form.get('new_total_pl')
    if not new_date_str or not new_pl_str:
        flash("日付またはPLが未入力です", "error")
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    try:
        dt = datetime.strptime(new_date_str, '%Y-%m-%d').date()
        if dt > datetime.now().date():
            flash("未来の日付は登録できません", "error")
            return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

        val_pl = float(new_pl_str)
    except ValueError:
        flash("日付や数値が不正です", "error")
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # 「同じ日付が既にある」&& 「それが自分とは別レコード」 なら上書きの扱い
    if dt != pl_rec.date:
        conflict = PLHistory.query.filter_by(
            generation_id=generation_id,
            group_id=group_id,
            date=dt
        ).first()
        if conflict and conflict.pl_history_id != pl_id:
            # 古いレコード(conflict)を消して pl_rec に差し替えるイメージ
            db.session.delete(conflict)

    # 上書き
    pl_rec.date = dt
    pl_rec.total_pl = val_pl

    db.session.commit()
    flash("PL履歴を更新しました", "success")
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/plhistory/<int:pl_id>/delete', methods=['GET'])
@login_required
def delete_plhistory(generation_id, group_id, pl_id):
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    pl_rec = PLHistory.query.filter_by(
        generation_id=generation_id,
        group_id=group_id,
        pl_history_id=pl_id
    ).first()
    if not pl_rec:
        abort(404, "該当PL履歴がありません")

    db.session.delete(pl_rec)
    db.session.commit()
    flash("PL履歴を削除しました", "success")
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))




@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/holding/<int:holding_id>/update', methods=['POST'])
@login_required
def update_holding(generation_id, group_id, holding_id):
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    holding = Holding.query.filter_by(generation_id=generation_id, group_id=group_id, holding_id=holding_id).first()
    if not holding:
        abort(404, "Holding not found")

    try:
        quantity = float(request.form.get('quantity'))
        buy_price = float(request.form.get('buy_price'))
        current_price = float(request.form.get('current_price'))
    except (TypeError, ValueError):
        flash('数量、買価格、現在価格は数値を入力してください', 'error')
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    holding.quantity = quantity
    holding.buy_price = buy_price
    holding.current_price = current_price
    holding.current_pl = (current_price - buy_price) * quantity - FIXED_COST

    db.session.commit()
    flash('保有銘柄を更新しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/holding/<int:holding_id>/delete', methods=['GET'])
@login_required
def delete_holding(generation_id, group_id, holding_id):
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    holding = Holding.query.filter_by(generation_id=generation_id, group_id=group_id, holding_id=holding_id).first()
    if not holding:
        abort(404, "Holding not found")

    db.session.delete(holding)
    db.session.commit()
    flash('保有銘柄を削除しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/sold/<int:sold_id>/update', methods=['POST'])
@login_required
def update_sold(generation_id, group_id, sold_id):
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    sold = Sold.query.filter_by(generation_id=generation_id, group_id=group_id, sold_id=sold_id).first()
    if not sold:
        abort(404, "Sold record not found")

    try:
        sold_quantity = float(request.form.get('sold_quantity'))
        buy_price = float(request.form.get('buy_price'))
        sell_price = float(request.form.get('sell_price'))
        trade_date_str = request.form.get('trade_date')
        trade_date = datetime.strptime(trade_date_str, '%Y-%m-%d')
    except (TypeError, ValueError):
        flash('数量、買価格、売価格、売却日付は正しい形式で入力してください', 'error')
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    sold.sold_quantity = sold_quantity
    sold.buy_price = buy_price
    sold.sell_price = sell_price
    sold.realized_pl = (sell_price - buy_price) * sold_quantity - FIXED_COST
    sold.trade_date = trade_date

    db.session.commit()
    flash('売却済み銘柄を更新しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/sold/<int:sold_id>/delete', methods=['GET'])
@login_required
def delete_sold(generation_id, group_id, sold_id):
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    sold = Sold.query.filter_by(generation_id=generation_id, group_id=group_id, sold_id=sold_id).first()
    if not sold:
        abort(404, "Sold record not found")

    db.session.delete(sold)
    db.session.commit()
    flash('売却済み銘柄を削除しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))
