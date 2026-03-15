#include<stdio.h>
int main(){
    unsigned int n,k32;
    unsigned long long k64;
    while(scanf("%d %llu",&n,&k64)!=EOF){
        if(n==64){
            printf("%llu\n",~k64);
        }
        else{
            k32=k64;
            printf("%u\n",~k32);
        }
    }
    return 0; 
}

