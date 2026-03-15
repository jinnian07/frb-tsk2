#include <stdio.h>
#include <stdlib.h>

// 违反命名规范（函数名应为snake_case）
int MyFunction(int param1, int Param2) {
    int localVar = 42;
    char *my_string = malloc(100); // 违反MISRA C:2012 Rule 21.7（禁用malloc）
    float f = 3.14;
    int i = f; // 违反MISRA C:2012 Rule 10.5（隐式类型转换）
    return localVar + Param2;
}

int main() {
    MyFunction(10, 20);
    return 0;
}