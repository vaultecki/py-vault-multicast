# py-vault-multicast

own python multicast wrapper, used in other projects to find other devices in local network
- publisher sends udp packets to multicast address
- publisher repeats message every x seconds
- no length check for message (can be discarded if too long)
- listener listens for udp packet on multicast address
- listener emmits pysingal when message is received
- 
- qt exmaple window for listener
