src=open('execution/broker.py',encoding='utf-8').read() 
idx=src.find('def poll') 
print(src[idx:idx+2000]) 
