// Package dsim is a deterministic discrete-event network simulator used
// throughout the distributed-systems course. Same seed => same run, so any
// bug (a lost commit after a partition, a split-brain election) reproduces
// exactly. No wall-clock, no real goroutine races: time advances in logical
// steps and message delivery order is fixed by the seed.
package dsim

import (
	"container/heap"
	"math/rand"
)

type NodeID int

// Message is what nodes exchange. Payload is any concrete message type.
type Message struct {
	From, To NodeID
	Payload  any
}

// Node is anything that reacts to delivered messages and to clock ticks.
// Handlers send new messages via the *Net passed in (never store it and use
// it off-schedule; that would break determinism).
type Node interface {
	OnMessage(m Message, net *Net)
	OnTick(now int, net *Net)
}

type event struct {
	at     int // logical delivery time
	seq    int // tiebreaker: FIFO within same time, keeps order deterministic
	m      Message
}

type eventQueue []event

func (q eventQueue) Len() int { return len(q) }
func (q eventQueue) Less(i, j int) bool {
	if q[i].at != q[j].at {
		return q[i].at < q[j].at
	}
	return q[i].seq < q[j].seq
}
func (q eventQueue) Swap(i, j int)      { q[i], q[j] = q[j], q[i] }
func (q *eventQueue) Push(x any)        { *q = append(*q, x.(event)) }
func (q *eventQueue) Pop() any {
	old := *q
	n := len(old)
	e := old[n-1]
	*q = old[:n-1]
	return e
}

// Net is the simulated network. All fault injection lives here.
type Net struct {
	rng     *rand.Rand
	nodes   map[NodeID]Node
	q       eventQueue
	now     int
	seq     int
	// fault injection
	part    map[NodeID]int  // partition group; nodes in different groups cannot talk
	crashed map[NodeID]bool // crashed nodes neither send nor receive
	drop    float64         // per-message drop probability
	minLat  int
	maxLat  int
	// observability
	Delivered int
	Dropped   int
}

func NewNet(seed int64) *Net {
	return &Net{
		rng:     rand.New(rand.NewSource(seed)),
		nodes:   map[NodeID]Node{},
		part:    map[NodeID]int{},
		crashed: map[NodeID]bool{},
		minLat:  1,
		maxLat:  1,
	}
}

func (n *Net) Add(id NodeID, node Node) { n.nodes[id] = node }

// Config knobs -------------------------------------------------------------
func (n *Net) SetLatency(min, max int) { n.minLat, n.maxLat = min, max }
func (n *Net) SetDropRate(p float64)   { n.drop = p }

// Partition puts each listed group of nodes into its own island. Nodes in
// different islands cannot exchange messages until Heal.
func (n *Net) Partition(groups ...[]NodeID) {
	n.part = map[NodeID]int{}
	for gi, g := range groups {
		for _, id := range g {
			n.part[id] = gi
		}
	}
}
func (n *Net) Heal()             { n.part = map[NodeID]int{} }
func (n *Net) Crash(id NodeID)   { n.crashed[id] = true }
func (n *Net) Restart(id NodeID) { n.crashed[id] = false }

func (n *Net) reachable(from, to NodeID) bool {
	if n.crashed[from] || n.crashed[to] {
		return false
	}
	if len(n.part) == 0 {
		return true
	}
	return n.part[from] == n.part[to]
}

// Send is called by nodes to emit a message. It is NOT delivered immediately;
// it is scheduled at now+latency, may be dropped, and is filtered by
// partition/crash state at SEND time (delivery is re-checked too).
func (n *Net) Send(m Message) {
	if !n.reachable(m.From, m.To) {
		n.Dropped++
		return
	}
	if n.drop > 0 && n.rng.Float64() < n.drop {
		n.Dropped++
		return
	}
	lat := n.minLat
	if n.maxLat > n.minLat {
		lat += n.rng.Intn(n.maxLat - n.minLat + 1)
	}
	n.seq++
	heap.Push(&n.q, event{at: n.now + lat, seq: n.seq, m: m})
}

// Run advances logical time up to maxSteps. On each step it (1) delivers all
// messages scheduled for the current time, re-checking reachability at
// delivery, then (2) ticks every live node. Returns the final logical time.
func (n *Net) Run(maxSteps int) int {
	for n.now < maxSteps {
		n.now++
		for len(n.q) > 0 && n.q[0].at == n.now {
			e := heap.Pop(&n.q).(event)
			if !n.reachable(e.m.From, e.m.To) {
				n.Dropped++
				continue
			}
			if node, ok := n.nodes[e.m.To]; ok {
				n.Delivered++
				node.OnMessage(e.m, n)
			}
		}
		for id, node := range n.nodes {
			if !n.crashed[id] {
				node.OnTick(n.now, n)
			}
		}
	}
	return n.now
}

func (n *Net) Now() int { return n.now }
