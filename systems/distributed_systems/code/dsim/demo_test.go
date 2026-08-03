package dsim

import "testing"

type pinger struct {
	id, peer NodeID
	count    int
	start    bool
}

func (p *pinger) OnMessage(m Message, net *Net) {
	p.count = m.Payload.(int) + 1
	net.Send(Message{From: p.id, To: p.peer, Payload: p.count})
}
func (p *pinger) OnTick(now int, net *Net) {
	if p.start && now == 1 {
		net.Send(Message{From: p.id, To: p.peer, Payload: 0})
	}
}

func TestPingPong(t *testing.T) {
	net := NewNet(42)
	a := &pinger{id: 0, peer: 1, start: true}
	b := &pinger{id: 1, peer: 0}
	net.Add(0, a); net.Add(1, b)
	net.Run(20)
	if a.count == 0 && b.count == 0 { t.Fatal("no messages flowed") }
	t.Logf("after 20 steps: a=%d b=%d delivered=%d", a.count, b.count, net.Delivered)
}

func TestPartitionIsolates(t *testing.T) {
	net := NewNet(1)
	a := &pinger{id: 0, peer: 1, start: true}
	b := &pinger{id: 1, peer: 0}
	net.Add(0, a); net.Add(1, b)
	net.Partition([]NodeID{0}, []NodeID{1})
	net.Send(Message{From: 0, To: 1, Payload: 0}) // try to cross the partition
	net.Run(20)
	if net.Delivered != 0 { t.Fatalf("partition leaked: delivered=%d", net.Delivered) }
	net.Heal()
	net.Send(Message{From: 0, To: 1, Payload: 0}) // re-inject after heal
	net.Run(40)
	if net.Delivered == 0 { t.Fatal("heal did not restore connectivity") }
	t.Logf("after heal: delivered=%d a=%d b=%d", net.Delivered, a.count, b.count)
}

func TestDeterministic(t *testing.T) {
	run := func() int {
		net := NewNet(7)
		net.SetLatency(1, 5)
		net.SetDropRate(0.1)
		a := &pinger{id: 0, peer: 1, start: true}
		b := &pinger{id: 1, peer: 0}
		net.Add(0, a); net.Add(1, b)
		net.Run(100)
		return a.count + b.count*1000 + net.Dropped*1000000
	}
	if run() != run() { t.Fatal("same seed produced different runs") }
	t.Logf("reproducible fingerprint: %d", run())
}
