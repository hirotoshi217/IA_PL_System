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

        return "不正なアクション", 400
    else:
        all_generations = Generation.query.all()
        return render_template('generation_edit.html', all_generations=all_generations)


@trade_bp.route('/generation/<int:generation_id>/groups')
@login_required
def generation_groups(generation_id):
    """
    ある期(generation_id)のグループ一覧 + 全グループ分のPL推移グラフ
    ★ 修正ここ: 閲覧は制限しない(誰でも見られる)
    (従来は "if current_user.generation_id != generation_id: return 403" していたが削除)
    """

    # 期を取得
    gen = Generation.query.get_or_404(generation_id)

    # 期に属するグループ一覧
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
    ★ 修正ここ: 閲覧のみの制限を外す(他期も見られる)
    """

    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")

    # 最新の合計PL
    latest_pl = PLHistory.query.filter_by(
        generation_id=generation_id,
        group_id=group_id
    ).order_by(PLHistory.pl_history_id.desc()).first()
    latest_total_pl = latest_pl.total_pl if latest_pl else 0.0

    holdings = Holding.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    solds = Sold.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    pl_history = PLHistory.query.filter_by(generation_id=generation_id, group_id=group_id)\
                                .order_by(PLHistory.date).all()

    # グラフ用データの準備
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
    ★ 修正ここ: adminか、あるいはuser.generation_id==generation_id のときのみOK
    """
    if current_user.role != 'admin':
        # userの場合: bs_inputは自分の期だけ操作可
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
        # 新規
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
    holdings = Holding.query.all()
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
    groups = Group.query.all()
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
    # アクセス権の確認: admin または user が自分の generation_id の場合
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    group_obj = Group.query.filter_by(generation_id=generation_id, group_id=group_id).first()
    if not group_obj:
        abort(404, "Group not found in this generation")

    # Generation オブジェクトを取得
    gen = Generation.query.get_or_404(generation_id)

    holdings = Holding.query.filter_by(generation_id=generation_id, group_id=group_id).all()
    solds = Sold.query.filter_by(generation_id=generation_id, group_id=group_id).all()

    return render_template('group_BS_edit.html',
                           generation_id=generation_id,
                           generation_name=gen.generation_name,  # 修正箇所
                           group=group_obj,
                           holdings=holdings,
                           solds=solds)


# 保有銘柄の更新
@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/holding/<int:holding_id>/update', methods=['POST'])
@login_required
def update_holding(generation_id, group_id, holding_id):
    """
    保有銘柄を更新する処理
    """
    # アクセス権の確認
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    holding = Holding.query.filter_by(generation_id=generation_id, group_id=group_id, holding_id=holding_id).first()
    if not holding:
        abort(404, "Holding not found")

    # フォームからデータ取得
    try:
        quantity = float(request.form.get('quantity'))
        buy_price = float(request.form.get('buy_price'))
        current_price = float(request.form.get('current_price'))
    except (TypeError, ValueError):
        flash('数量、買価格、現在価格は数値を入力してください', 'error')
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # データの更新
    holding.quantity = quantity
    holding.buy_price = buy_price
    holding.current_price = current_price
    holding.current_pl = (current_price - buy_price) * quantity - FIXED_COST

    db.session.commit()
    flash('保有銘柄を更新しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


# 保有銘柄の削除
@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/holding/<int:holding_id>/delete', methods=['GET'])
@login_required
def delete_holding(generation_id, group_id, holding_id):
    """
    保有銘柄を削除する処理
    """
    # アクセス権の確認
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    holding = Holding.query.filter_by(generation_id=generation_id, group_id=group_id, holding_id=holding_id).first()
    if not holding:
        abort(404, "Holding not found")

    db.session.delete(holding)
    db.session.commit()
    flash('保有銘柄を削除しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


# 売却済み銘柄の更新
@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/sold/<int:sold_id>/update', methods=['POST'])
@login_required
def update_sold(generation_id, group_id, sold_id):
    """
    売却済み銘柄を更新する処理
    """
    # アクセス権の確認
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    sold = Sold.query.filter_by(generation_id=generation_id, group_id=group_id, sold_id=sold_id).first()
    if not sold:
        abort(404, "Sold record not found")

    # フォームからデータ取得
    try:
        sold_quantity = float(request.form.get('sold_quantity'))
        buy_price = float(request.form.get('buy_price'))
        sell_price = float(request.form.get('sell_price'))
        trade_date_str = request.form.get('trade_date')
        trade_date = datetime.strptime(trade_date_str, '%Y-%m-%d')
    except (TypeError, ValueError):
        flash('数量、買価格、売価格、売却日付は正しい形式で入力してください', 'error')
        return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))

    # データの更新
    sold.sold_quantity = sold_quantity
    sold.buy_price = buy_price
    sold.sell_price = sell_price
    sold.realized_pl = (sell_price - buy_price) * sold_quantity - FIXED_COST
    sold.trade_date = trade_date

    db.session.commit()
    flash('売却済み銘柄を更新しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))


# 売却済み銘柄の削除
@trade_bp.route('/generation/<int:generation_id>/group/<int:group_id>/sold/<int:sold_id>/delete', methods=['GET'])
@login_required
def delete_sold(generation_id, group_id, sold_id):
    """
    売却済み銘柄を削除する処理
    """
    # アクセス権の確認
    if current_user.role != 'admin' and current_user.generation_id != generation_id:
        return "アクセス権がありません", 403

    sold = Sold.query.filter_by(generation_id=generation_id, group_id=group_id, sold_id=sold_id).first()
    if not sold:
        abort(404, "Sold record not found")

    db.session.delete(sold)
    db.session.commit()
    flash('売却済み銘柄を削除しました', 'success')
    return redirect(url_for('trade.group_bs_edit', generation_id=generation_id, group_id=group_id))