Thay vì chỉ hiển thị text trả lời người dùng, thì chatbot phải trực quan hoá được câu trả lời bằng html. 
Người dùng vẫn nhập câu hỏi như bình thường: ví dụ người dùng nhập câu hỏi" Thời tiết hà nội hôm nay thế nào", và được quyền chọn template để trực quan hoá câu trả lời của bot, giả sử người dùng chọn template thời tiết cơ bản.
Thì sẽ kiểm tra xem template đó đã tồn tại chưa, nếu chưa thì sinh template theo ý muốn người dùng, nếu có rồi thì tái sử dụng lại. Và kết quả text của bot sẽ hiển thị lên template được chọn để visualize cho người dùng ( dưới dạng html, mà người dùng xem được như kiểu web)


Tính toán việc sử dụng model local, prefill bao nhiêu token/s, generation bao nhiêu token/s -> với dữ liệu nào thì cho vào prompt-> caching được, tốc độ tốt hơn không?, chính xác cao hơn không?. lưu data xong truy xuất suy luận hay cho vào prompt? hay pipline?...
Bài toán chính: người dùng input vào dữ liệu chung -> nhận text -> text được visualize. template sẽ theo database sẵn -> template lưu thế nào... (sử dụng model local)



