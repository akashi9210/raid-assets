import struct,sys
M=(1<<64)-1
def mix(a,b,c):
    a=(a-b-c)&M;a^=c>>43
    b=(b-c-a)&M;b^=(a<<9)&M
    c=(c-a-b)&M;c^=b>>8
    a=(a-b-c)&M;a^=c>>38
    b=(b-c-a)&M;b^=(a<<23)&M
    c=(c-a-b)&M;c^=b>>5
    a=(a-b-c)&M;a^=c>>35
    b=(b-c-a)&M;b^=(a<<49)&M
    c=(c-a-b)&M;c^=b>>11
    a=(a-b-c)&M;a^=c>>12
    b=(b-c-a)&M;b^=(a<<18)&M
    c=(c-a-b)&M;c^=b>>22
    return a,b,c
def hash64(s,level=0):
    k=s.encode();n=len(k);length=n
    a=b=level;c=0x9e3779b97f4a7c13
    while n>=24:
        a=(a+struct.unpack_from('<Q',k,length-n)[0])&M
        b=(b+struct.unpack_from('<Q',k,length-n+8)[0])&M
        c=(c+struct.unpack_from('<Q',k,length-n+16)[0])&M
        a,b,c=mix(a,b,c);n-=24
    t=k[length-n:]
    c=(c+length)&M
    t=t+b'\0'*(24-len(t))
    # bytes 0-7 -> a, 8-15 -> b, 16-22 -> c<<8
    a=(a+struct.unpack_from('<Q',t,0)[0])&M
    b=(b+struct.unpack_from('<Q',t,8)[0])&M
    c=(c+(struct.unpack_from('<Q',t,16)[0]<<8&M))&M
    a,b,c=mix(a,b,c)
    return c
