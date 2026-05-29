arr = [2, 5, 8, 12, 16, 23, 38, 56, 72, 91]
key = 23


def binary_search(arr: list[int], key: int):
    low = 0
    high = len(arr) - 1
    mid = low + (high - low) // 2
    while high >= low:
        if arr[mid] == key:
            return mid
        elif key < arr[mid]:
            high = mid
        else:
            low = mid
        low += 1
        high -= 1


result = binary_search(arr, key)
print(result)
