import re 
src=open('execution/broker.py',encoding='utf-8').read() 
idx=src.find('poll') 
print(src[idx:idx+1500]) 
