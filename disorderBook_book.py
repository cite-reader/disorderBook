import bisect, datetime, json


def current_timestamp():
	ts = str(datetime.datetime.utcnow().isoformat())		# Thanks to medecau for this
	return ts
	

class Order (dict):
	def __init__(self, **kwargs):
		super().__init__(**kwargs)
	
	# All the comparisons are just for bisection insorting. Order should compare lower if it has higher
	# priority, which is confusing but whatever. It means high priority orders are sorted first.
	
	def __eq__(self, other):
		if self["price"] == other["price"] and self["ts"] == other["ts"]:
			return True
		else:
			return False
	
	def __lt__(self, other):
		if self["direction"] == "buy":
			if self["price"] > other["price"]:		# We beat the other price, so we are "less" (low is better)
				return True
			elif self["price"] < other["price"]:	# Other price beats us, so we are "more" (high is worse)
				return False
			elif self["ts"] < other["ts"]:			# Our order was first, so we are "less" (low is better)
				return True
			else:
				return False
		else:
			if self["price"] < other["price"]:		# We beat the other price, so we are "less" (low is better)
				return True
			elif self["price"] > other["price"]:	# Other price beats us, so we are "more" (high is worse)
				return False
			elif self["ts"] < other["ts"]:			# Our order was first, so we are "less" (low is better)
				return True
			else:
				return False
	
	def __le__(self, other):
		if self < other or self == other:
			return True
		else:
			return False
	
	def __gt__(self, other):
		if self <= other:
			return False
		else:
			return True
	
	def __ge__(self, other):
		if self > other or self == other:
			return True
		else:
			return False
	
	def __ne__(self, other):
		if not self == other:
			return True
		else:
			return False

	# This is the actual meat of the trade-making algorithm...
	
	def standing_cross(self, other, timestamp, book):		# Meaning this object is the standing order
		quantity = min(self["qty"], other["qty"])
		self["qty"] -= quantity
		self["totalFilled"] += quantity
		other["qty"] -= quantity
		other["totalFilled"] += quantity

		book.last_trade_time = timestamp
		book.last_trade_price = self["price"]
		book.last_trade_size = quantity
		
		fill = dict(price = self["price"], qty = quantity, ts = timestamp)
		
		for o in self, other:
			o["fills"].append(fill)
			if o["qty"] == 0:
				o["open"] = False


# For the orderbook itself, the general plan is to keep a list of bids and a list of asks,
# always *kept* sorted (never sorted as a whole), with the top priority order first in line.
# Incoming orders can then just iterate through the list until they're finished crossing.

class OrderBook ():
	def __init__(self, venue, symbol):
		self.venue = str(venue)
		self.symbol = str(symbol)
		self.bids = []
		self.asks = []
		self.id_lookup_table = dict()			# order id ---> order object
		self.account_order_lists = dict()		# account name ---> list of order objects
		self.next_id = 0
		self.quote = dict()
		self.last_trade_time = None
		self.last_trade_price = None
		self.last_trade_size = None


	def account_from_order_id(self, id):
		try:
			return self.id_lookup_table[int(id)]["account"]
		except KeyError:
			return None


	def cleanup_closed_orders(self):		# This is cute but is it fast?... If improving this later, remember
		self.bids = [bid for bid in self.bids if bid["open"]]		# that cancels can call this, meaning that
		self.asks = [ask for ask in self.asks if ask["open"]]		# closed orders could be anywhere.
	

	def get_book(self):
		ret = dict()
		ret["ok"] = True
		ret["venue"] = self.venue
		ret["symbol"] = self.symbol
		ret["bids"] = [{"price" : order["price"], "qty": order["qty"], "isBuy": True} for order in self.bids]
		ret["asks"] = [{"price" : order["price"], "qty": order["qty"], "isBuy": False} for order in self.asks]
		ret["ts"] = current_timestamp()
		return ret
	
	
	def get_status(self, id):
		return self.id_lookup_table[int(id)]
	
	
	def get_all_orders(self, account):
		if account in self.account_order_lists:
			return {"ok" : True, "venue" : self.venue, "orders" : self.account_order_lists[account]}
		else:
			return {"ok" : True, "venue" : self.venue, "orders" : []}
	

	def get_quote(self):
		self.set_quote()
		return self.quote
	

	def set_quote(self):					# Could optimise (?) by changing everything every
		self.quote["ok"] = True				# fill, but is that really faster in practice?
		self.quote["venue"] = self.venue
		self.quote["symbol"] = self.symbol
		
		if self.bids:
			self.quote["bidDepth"] = self.bid_depth()
			self.quote["bidSize"] = self.bid_size()
			self.quote["bid"] = self.bids[0]["price"]
		else:
			self.quote["bidDepth"] = 0
			self.quote["bidSize"] = 0
			if "bid" in self.quote:
				self.quote.pop("bid")
		
		if self.asks:
			self.quote["askDepth"] = self.ask_depth()
			self.quote["askSize"] = self.ask_size()
			self.quote["ask"] = self.asks[0]["price"]
		else:
			self.quote["askDepth"] = 0
			self.quote["askSize"] = 0
			if "ask" in self.quote:
				self.quote.pop("ask")
		
		if self.last_trade_price is not None:
			self.quote["last"] = self.last_trade_price
			self.quote["lastSize"] = self.last_trade_size
			self.quote["lastTrade"] = self.last_trade_time
		
		self.quote["quoteTime"] = current_timestamp()

		
	def bid_size(self):
		if len(self.bids) == 0:
			return 0
		ret = 0
		bestprice = self.bids[0]["price"]
		for order in self.bids:
			if order["price"] == bestprice:
				ret += order["qty"]
			else:
				break
		return ret

		
	def ask_size(self):
		if len(self.asks) == 0:
			return 0
		ret = 0
		bestprice = self.asks[0]["price"]
		for order in self.asks:
			if order["price"] == bestprice:
				ret += order["qty"]
			else:
				break
		return ret

		
	def bid_depth(self):			# Could optimise by just storing this whenever it changes
		ret = 0
		for order in self.bids:
			ret += order["qty"]
		return ret

	
	def ask_depth(self):			# Could optimise by just storing this whenever it changes
		ret = 0
		for order in self.asks:
			ret += order["qty"]
		return ret


	def fok_can_buy(self, price, qty):
		avail = 0
		for standing in self.asks:
			if standing["price"] <= price:
				avail += standing["qty"]
				if avail >= qty:
					break
			else:
				break				# Taking advantage of list sortedness

		if avail >= qty:
			return True
		else:
			return False


	def fok_can_sell(self, price, qty):
		avail = 0
		for standing in self.bids:
			if standing["price"] >= price:
				avail += standing["qty"]
				if avail >= qty:
					break
			else:
				break				# Taking advantage of list sortedness

		if avail >= qty:
			return True
		else:
			return False
	
	
	def cancel_order(self, id):
		order = self.id_lookup_table[int(id)]
		if order["open"]:
			order["qty"] = 0
			order["open"] = False
			self.cleanup_closed_orders()
		return order
	
	
	def parse_order(self, info):

		# Thanks to cite-reader for this:
		# Match behavior of real Stockfighter: recognize both `symbol` and `stock`.
		if "stock" in info:
			info["symbol"] = info["stock"]

		assert(info["venue"] == self.venue)
		assert(info["symbol"] == self.symbol)
		assert(info["direction"] in ["buy", "sell"])
		assert(info["qty"] > 0)
		assert(info["price"] >= 0)
		assert(info["orderType"] in ["limit", "market", "fill-or-kill", "immediate-or-cancel"])
		assert(info["account"])
		
		order = Order(
				ok			= True,
				venue		= self.venue,
				symbol		= self.symbol,
				direction	= info["direction"],
				originalQty	= info["qty"],
				qty			= info["qty"],
				price		= info["price"],
				orderType	= info["orderType"],
				id			= self.next_id,
				account		= info["account"],
				ts			= current_timestamp(),
				fills		= list(),
				totalFilled	= 0,
				open		= True
				)
		
		self.next_id += 1
		
		self.id_lookup_table[order["id"]] = order			# So we can find it for status/cancel
		
		if order["account"] not in self.account_order_lists:
			self.account_order_lists[order["account"]] = list()
		self.account_order_lists[order["account"]].append(order)		# So we can list all an account's orders
			
		# Limit and IOC orders are easy...
		
		if order["orderType"] == "limit" or order["orderType"] == "immediate-or-cancel":
			self.run_order(order)
			
		# FOK orders are slightly tricky...
		
		elif order["orderType"] == "fill-or-kill":
			if order["direction"] == "buy":
				if self.fok_can_buy(price = order["price"], qty = order["qty"]):
					self.run_order(order)
			else:
				if self.fok_can_sell(price = order["price"], qty = order["qty"]):
					self.run_order(order)
		
		# Market orders are slightly tricky...
		
		elif order["orderType"] == "market":
			actually_stated_price = order["price"]
			
			if order["direction"] == "buy":
				if self.asks:									# We use the cheap trick of temporarily setting
					order["price"] = self.asks[-1]["price"]		# the order's price to the worst one on the book
			else:
				if self.bids:
					order["price"] = self.bids[-1]["price"]
			
			self.run_order(order)
			
			order["price"] = actually_stated_price
		
		return order


	def run_order(self, order):
	
		incomingprice = order["price"]
		timestamp = current_timestamp()
	
		if order["direction"] == "sell":
			for standing in self.bids:
				if standing["price"] >= incomingprice:
					standing.standing_cross(order, timestamp, self)
					if order["qty"] == 0:
						break
				else:
					break		# Taking advantage of the sortedness of the book's lists
		else:
			for standing in self.asks:
				if standing["price"] <= incomingprice:
					standing.standing_cross(order, timestamp, self)
					if order["qty"] == 0:
						break
				else:
					break
		
		self.cleanup_closed_orders()
		
		if order["orderType"] == "limit":		# Only limit orders rest on the book
			if order["open"]:
				if order["direction"] == "buy":
					bisect.insort(self.bids, order)
				else:
					bisect.insort(self.asks, order)
		
		return order
