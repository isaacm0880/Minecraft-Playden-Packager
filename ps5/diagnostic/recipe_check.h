#ifndef PS5_RECIPE_CHECK_H
#define PS5_RECIPE_CHECK_H
#include <ctype.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#define RECIPE_MAX 16384
typedef struct {const char *s; size_t n,p; int depth;} J;
static void skipws(J *j){while(j->p<j->n && (j->s[j->p]==' '||j->s[j->p]=='\n'||j->s[j->p]=='\r'||j->s[j->p]=='\t'))++j->p;}
static int stringval(J *j,size_t *start,size_t *end){
 skipws(j);if(j->p>=j->n||j->s[j->p++]!='"')return 0;*start=j->p;
 while(j->p<j->n){unsigned char c=(unsigned char)j->s[j->p++];
  if(c=='"'){*end=j->p-1;return 1;}if(c<32)return 0;
  if(c=='\\'){if(j->p>=j->n)return 0;c=(unsigned char)j->s[j->p++];
   if(strchr("\"\\/bfnrt",c))continue;
   if(c!='u'||j->n-j->p<4)return 0;
   for(int i=0;i<4;i++)if(!isxdigit((unsigned char)j->s[j->p++]))return 0;
  }
 }return 0;
}
static int value(J *j);
static int object(J *j){
 if(++j->depth>32)return 0;
 ++j->p;skipws(j);
 if(j->p<j->n&&j->s[j->p]=='}'){++j->p;--j->depth;return 1;}
 for(;;){size_t a,b;if(!stringval(j,&a,&b))return 0;skipws(j);
  if(j->p>=j->n||j->s[j->p++]!=':')return 0;
  if(!value(j))return 0;
  skipws(j);if(j->p>=j->n)return 0;
  char c=j->s[j->p++];if(c=='}'){--j->depth;return 1;}if(c!=',')return 0;
 }
}
static int array(J *j){
 if(++j->depth>32)return 0;
 ++j->p;skipws(j);
 if(j->p<j->n&&j->s[j->p]==']'){++j->p;--j->depth;return 1;}
 for(;;){if(!value(j))return 0;
  skipws(j);if(j->p>=j->n)return 0;
  char c=j->s[j->p++];if(c==']'){--j->depth;return 1;}if(c!=',')return 0;}
}
static int value(J *j){
 skipws(j);if(j->p>=j->n)return 0;char c=j->s[j->p];
 if(c=='{')return object(j);
 if(c=='[')return array(j);
 if(c=='"'){size_t a,b;return stringval(j,&a,&b);}
 const char *lit=c=='t'?"true":c=='f'?"false":c=='n'?"null":NULL;
 if(lit){size_t n=strlen(lit);if(j->n-j->p<n||memcmp(j->s+j->p,lit,n))return 0;j->p+=n;return 1;}
 if(c=='-')++j->p;
 if(j->p>=j->n)return 0;
 if(j->s[j->p]=='0')++j->p;
 else if(j->s[j->p]>='1'&&j->s[j->p]<='9'){do{++j->p;}while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]));}
 else return 0;
 if(j->p<j->n&&j->s[j->p]=='.'){++j->p;size_t a=j->p;while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]))++j->p;if(a==j->p)return 0;}
 if(j->p<j->n&&(j->s[j->p]=='e'||j->s[j->p]=='E')){++j->p;if(j->p<j->n&&(j->s[j->p]=='+'||j->s[j->p]=='-'))++j->p;size_t a=j->p;while(j->p<j->n&&isdigit((unsigned char)j->s[j->p]))++j->p;if(a==j->p)return 0;}
 return 1;
}
static int keyval(const char *s,size_t n,const char *key,const char **out,size_t *len){
 J j={s,n,0,0};skipws(&j);if(j.p>=n||s[j.p++]!='{')return 0;skipws(&j);
 while(j.p<n&&s[j.p]!='}'){size_t a,b;if(!stringval(&j,&a,&b))return 0;skipws(&j);
  if(j.p>=n||s[j.p++]!=':')return 0;
  skipws(&j);size_t p=j.p;
  if(!value(&j))return 0;
  if(strlen(key)==b-a&&!memcmp(s+a,key,b-a)){*out=s+p;*len=j.p-p;return 1;}
  skipws(&j);if(j.p>=n||s[j.p++]!=',')break;skipws(&j);
 }return 0;
}
static int field(const char *s,size_t n,const char *key,const char *expected){
 const char *v;size_t z;return keyval(s,n,key,&v,&z)&&z==strlen(expected)&&!memcmp(v,expected,z);
}
static int nested(const char *s,size_t n,const char *parent,const char *key,const char *expected){
 const char *v;size_t z;return keyval(s,n,parent,&v,&z)&&field(v,z,key,expected);
}
static int recipe_check(const char *folder,char *reason,size_t cap){
 char path[1024];struct stat st;
 int n=snprintf(path,sizeof(path),"%s/recipe.json",folder);
 if(n<0||(size_t)n>=sizeof(path)){snprintf(reason,cap,"path-too-long");return 0;}
 if(lstat(path,&st)||!S_ISREG(st.st_mode)){snprintf(reason,cap,"recipe-missing");return 0;}
 if(st.st_size<=0||st.st_size>RECIPE_MAX){snprintf(reason,cap,"recipe-size-invalid");return 0;}
 FILE *f=fopen(path,"rb");if(!f){snprintf(reason,cap,"recipe-unreadable");return 0;}
 char buf[RECIPE_MAX+1];size_t z=fread(buf,1,sizeof(buf),f);int err=ferror(f);fclose(f);
 if(err||z==0||z>RECIPE_MAX){snprintf(reason,cap,"recipe-read-failed");return 0;}
 J j={buf,z,0,0};skipws(&j);if(j.p>=z||buf[j.p]!='{'||!value(&j)){snprintf(reason,cap,"recipe-invalid-json");return 0;}
 skipws(&j);if(j.p!=z){snprintf(reason,cap,"recipe-invalid-json");return 0;}
 if(!field(buf,z,"recipe_version","1")||!field(buf,z,"architecture","\"pe64\"")||
    !field(buf,z,"graphics","\"opengl\"")||!field(buf,z,"executable","\"java/bin/java.exe\"")||
    !nested(buf,z,"game","store","\"imported\"")||
    !nested(buf,z,"community","format_version","1")){
   snprintf(reason,cap,"recipe-contract-mismatch");return 0;}
 const char *v;size_t vn;
 if(!keyval(buf,z,"launch_entries",&v,&vn)||vn<2||v[0]!='['){snprintf(reason,cap,"recipe-launch-missing");return 0;}
 J a={v,vn,1,0};skipws(&a);size_t start=a.p;
 if(a.p>=vn||v[a.p]!='{'||!value(&a)){snprintf(reason,cap,"recipe-launch-invalid");return 0;}
 if(!field(v+start,a.p-start,"executable","\"java/bin/java.exe\"")||
    !field(v+start,a.p-start,"arguments","\"@../../playden.args\"")){
   snprintf(reason,cap,"recipe-launch-mismatch");return 0;}
 snprintf(reason,cap,"ok");return 1;
}
#endif
