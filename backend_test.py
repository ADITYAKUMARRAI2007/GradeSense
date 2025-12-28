#!/usr/bin/env python3

import requests
import sys
import json
from datetime import datetime, timedelta
import subprocess
import os

class GradeSenseAPITester:
    def __init__(self):
        self.base_url = "https://handgrade-pro.preview.emergentagent.com/api"
        self.session_token = None
        self.user_id = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, success, details=""):
        """Log test result"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
            print(f"✅ {name} - PASSED")
        else:
            print(f"❌ {name} - FAILED: {details}")
        
        self.test_results.append({
            "test": name,
            "success": success,
            "details": details
        })

    def run_api_test(self, name, method, endpoint, expected_status, data=None, headers=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        test_headers = {'Content-Type': 'application/json'}
        
        if self.session_token:
            test_headers['Authorization'] = f'Bearer {self.session_token}'
        
        if headers:
            test_headers.update(headers)

        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=test_headers, timeout=10)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=test_headers, timeout=10)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=test_headers, timeout=10)
            elif method == 'DELETE':
                response = requests.delete(url, headers=test_headers, timeout=10)

            print(f"   Status: {response.status_code}")
            
            success = response.status_code == expected_status
            details = ""
            
            if not success:
                details = f"Expected {expected_status}, got {response.status_code}"
                try:
                    error_data = response.json()
                    details += f" - {error_data.get('detail', 'No error details')}"
                except:
                    details += f" - Response: {response.text[:200]}"
            
            self.log_test(name, success, details)
            
            if success:
                try:
                    return response.json()
                except:
                    return {"status": "success"}
            else:
                return None

        except Exception as e:
            self.log_test(name, False, f"Request failed: {str(e)}")
            return None

    def create_test_user_and_session(self):
        """Create test user and session in MongoDB"""
        print("\n🔧 Creating test user and session in MongoDB...")
        
        timestamp = int(datetime.now().timestamp())
        self.user_id = f"test-user-{timestamp}"
        self.session_token = f"test_session_{timestamp}"
        
        # Create MongoDB commands
        mongo_commands = f"""
use('test_database');
var userId = '{self.user_id}';
var sessionToken = '{self.session_token}';
var expiresAt = new Date(Date.now() + 7*24*60*60*1000);

// Insert test user
db.users.insertOne({{
  user_id: userId,
  email: 'test.user.{timestamp}@example.com',
  name: 'Test Teacher',
  picture: 'https://via.placeholder.com/150',
  role: 'teacher',
  batches: [],
  created_at: new Date().toISOString()
}});

// Insert session
db.user_sessions.insertOne({{
  user_id: userId,
  session_token: sessionToken,
  expires_at: expiresAt.toISOString(),
  created_at: new Date().toISOString()
}});

print('Test user and session created successfully');
print('User ID: ' + userId);
print('Session Token: ' + sessionToken);
"""
        
        try:
            # Write commands to temp file
            with open('/tmp/mongo_setup.js', 'w') as f:
                f.write(mongo_commands)
            
            # Execute MongoDB commands
            result = subprocess.run([
                'mongosh', '--quiet', '--file', '/tmp/mongo_setup.js'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"✅ Test user created: {self.user_id}")
                print(f"✅ Session token: {self.session_token}")
                return True
            else:
                print(f"❌ MongoDB setup failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ Error creating test user: {str(e)}")
            return False

    def test_health_check(self):
        """Test health endpoint"""
        return self.run_api_test(
            "Health Check",
            "GET",
            "health",
            200
        )

    def test_auth_me(self):
        """Test auth/me endpoint"""
        return self.run_api_test(
            "Auth Me",
            "GET", 
            "auth/me",
            200
        )

    def test_create_batch(self):
        """Test batch creation"""
        batch_data = {
            "name": f"Mathematics Grade 10 {datetime.now().strftime('%H%M%S')}"
        }
        result = self.run_api_test(
            "Create Batch",
            "POST",
            "batches",
            200,
            data=batch_data
        )
        if result:
            self.test_batch_id = result.get('batch_id')
            self.test_batch_name = batch_data["name"]
        return result

    def test_duplicate_batch_prevention(self):
        """Test duplicate batch name prevention"""
        if not hasattr(self, 'test_batch_name'):
            print("⚠️  Skipping duplicate batch test - no batch created")
            return None
            
        # Try to create batch with same name
        duplicate_data = {"name": self.test_batch_name}
        return self.run_api_test(
            "Duplicate Batch Prevention",
            "POST",
            "batches",
            400,  # Should fail with 400
            data=duplicate_data
        )

    def test_update_batch(self):
        """Test batch name update"""
        if not hasattr(self, 'test_batch_id'):
            print("⚠️  Skipping batch update test - no batch created")
            return None
            
        update_data = {
            "name": f"Updated Mathematics Grade 10 {datetime.now().strftime('%H%M%S')}"
        }
        return self.run_api_test(
            "Update Batch Name",
            "PUT",
            f"batches/{self.test_batch_id}",
            200,
            data=update_data
        )

    def test_get_batch_details(self):
        """Test get batch details with students list"""
        if not hasattr(self, 'test_batch_id'):
            print("⚠️  Skipping batch details test - no batch created")
            return None
            
        return self.run_api_test(
            "Get Batch Details",
            "GET",
            f"batches/{self.test_batch_id}",
            200
        )

    def test_delete_empty_batch(self):
        """Test deleting empty batch (should succeed)"""
        # Create a temporary batch for deletion
        temp_batch_data = {
            "name": f"Temp Delete Batch {datetime.now().strftime('%H%M%S')}"
        }
        temp_result = self.run_api_test(
            "Create Temp Batch for Deletion",
            "POST",
            "batches",
            200,
            data=temp_batch_data
        )
        
        if temp_result:
            temp_batch_id = temp_result.get('batch_id')
            return self.run_api_test(
                "Delete Empty Batch",
                "DELETE",
                f"batches/{temp_batch_id}",
                200
            )
        return None

    def test_get_batches(self):
        """Test get batches"""
        return self.run_api_test(
            "Get Batches",
            "GET",
            "batches", 
            200
        )

    def test_create_subject(self):
        """Test subject creation"""
        subject_data = {
            "name": f"Test Subject {datetime.now().strftime('%H%M%S')}"
        }
        result = self.run_api_test(
            "Create Subject",
            "POST",
            "subjects",
            200,
            data=subject_data
        )
        if result:
            self.test_subject_id = result.get('subject_id')
        return result

    def test_get_subjects(self):
        """Test get subjects"""
        return self.run_api_test(
            "Get Subjects",
            "GET",
            "subjects",
            200
        )

    def test_create_student(self):
        """Test student creation"""
        timestamp = datetime.now().strftime('%H%M%S')
        student_data = {
            "email": f"sarah.johnson.{timestamp}@school.edu",
            "name": "Sarah Johnson",
            "role": "student",
            "student_id": f"STU{timestamp}",
            "batches": [self.test_batch_id] if hasattr(self, 'test_batch_id') else []
        }
        result = self.run_api_test(
            "Create Student",
            "POST",
            "students",
            200,
            data=student_data
        )
        if result:
            self.test_student_id = result.get('user_id')
        return result

    def test_student_analytics_api(self):
        """Test student analytics dashboard endpoint"""
        # Create a test student session first
        timestamp = int(datetime.now().timestamp())
        student_user_id = f"test-student-{timestamp}"
        student_session_token = f"student_session_{timestamp}"
        
        # Create student user and session in MongoDB
        mongo_commands = f"""
use('test_database');
var studentUserId = '{student_user_id}';
var studentSessionToken = '{student_session_token}';
var expiresAt = new Date(Date.now() + 7*24*60*60*1000);

// Insert test student
db.users.insertOne({{
  user_id: studentUserId,
  email: 'test.student.analytics.{timestamp}@example.com',
  name: 'Test Student Analytics',
  picture: 'https://via.placeholder.com/150',
  role: 'student',
  batches: [],
  created_at: new Date().toISOString()
}});

// Insert student session
db.user_sessions.insertOne({{
  user_id: studentUserId,
  session_token: studentSessionToken,
  expires_at: expiresAt.toISOString(),
  created_at: new Date().toISOString()
}});

print('Test student created for analytics test');
"""
        
        try:
            with open('/tmp/mongo_student_setup.js', 'w') as f:
                f.write(mongo_commands)
            
            result = subprocess.run([
                'mongosh', '--quiet', '--file', '/tmp/mongo_student_setup.js'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                # Test student analytics with student session
                original_token = self.session_token
                self.session_token = student_session_token
                
                analytics_result = self.run_api_test(
                    "Student Analytics Dashboard",
                    "GET",
                    "analytics/student-dashboard",
                    200
                )
                
                # Restore original session
                self.session_token = original_token
                return analytics_result
            else:
                print(f"❌ Failed to create test student: {result.stderr}")
                return None
                
        except Exception as e:
            print(f"❌ Error in student analytics test: {str(e)}")
            return None

    def test_detailed_student_analytics(self):
        """Test detailed student performance analytics for teachers"""
        if not hasattr(self, 'test_student_id'):
            print("⚠️  Skipping detailed student analytics - no student created")
            return None
            
        return self.run_api_test(
            "Detailed Student Analytics",
            "GET",
            f"students/{self.test_student_id}",
            200
        )

    def test_get_students(self):
        """Test get students"""
        return self.run_api_test(
            "Get Students",
            "GET",
            "students",
            200
        )

    def test_create_exam_with_subquestions(self):
        """Test exam creation with sub-questions"""
        # Need batch and subject first
        if not hasattr(self, 'test_batch_id') or not hasattr(self, 'test_subject_id'):
            print("⚠️  Skipping exam creation - missing batch or subject")
            return None
            
        exam_data = {
            "batch_id": self.test_batch_id,
            "subject_id": self.test_subject_id,
            "exam_type": "Unit Test",
            "exam_name": f"Algebra Fundamentals {datetime.now().strftime('%H%M%S')}",
            "total_marks": 100.0,
            "exam_date": "2024-01-15",
            "grading_mode": "balanced",
            "questions": [
                {
                    "question_number": 1,
                    "max_marks": 50.0,
                    "rubric": "Solve algebraic equations",
                    "sub_questions": [
                        {
                            "sub_id": "a",
                            "max_marks": 25.0,
                            "rubric": "Solve for x: 2x + 5 = 15"
                        },
                        {
                            "sub_id": "b", 
                            "max_marks": 25.0,
                            "rubric": "Solve for y: 3y - 7 = 14"
                        }
                    ]
                },
                {
                    "question_number": 2,
                    "max_marks": 50.0,
                    "rubric": "Quadratic equations",
                    "sub_questions": [
                        {
                            "sub_id": "a",
                            "max_marks": 30.0,
                            "rubric": "Find roots of x² - 5x + 6 = 0"
                        },
                        {
                            "sub_id": "b",
                            "max_marks": 20.0,
                            "rubric": "Graph the parabola"
                        }
                    ]
                }
            ]
        }
        result = self.run_api_test(
            "Create Exam with Sub-questions",
            "POST",
            "exams",
            200,
            data=exam_data
        )
        if result:
            self.test_exam_id = result.get('exam_id')
        return result

    def test_grading_modes(self):
        """Test different grading modes"""
        if not hasattr(self, 'test_batch_id') or not hasattr(self, 'test_subject_id'):
            print("⚠️  Skipping grading modes test - missing batch or subject")
            return None

        grading_modes = ["strict", "balanced", "conceptual", "lenient"]
        results = []
        
        for mode in grading_modes:
            exam_data = {
                "batch_id": self.test_batch_id,
                "subject_id": self.test_subject_id,
                "exam_type": "Quiz",
                "exam_name": f"Grading Test {mode} {datetime.now().strftime('%H%M%S')}",
                "total_marks": 50.0,
                "exam_date": "2024-01-15",
                "grading_mode": mode,
                "questions": [
                    {
                        "question_number": 1,
                        "max_marks": 50.0,
                        "rubric": f"Test question for {mode} grading"
                    }
                ]
            }
            result = self.run_api_test(
                f"Create Exam - {mode.title()} Mode",
                "POST",
                "exams",
                200,
                data=exam_data
            )
            results.append(result)
        
        return results

    def test_get_exams(self):
        """Test get exams"""
        return self.run_api_test(
            "Get Exams",
            "GET",
            "exams",
            200
        )

    def test_dashboard_analytics(self):
        """Test dashboard analytics"""
        return self.run_api_test(
            "Dashboard Analytics",
            "GET",
            "analytics/dashboard",
            200
        )

    def test_class_report(self):
        """Test class report"""
        return self.run_api_test(
            "Class Report",
            "GET",
            "analytics/class-report",
            200
        )

    def test_submissions_api(self):
        """Test submissions API for both teacher and student views"""
        # Test teacher view
        teacher_result = self.run_api_test(
            "Get Submissions (Teacher)",
            "GET",
            "submissions",
            200
        )
        
        # Test with batch filtering
        if hasattr(self, 'test_batch_id'):
            batch_filter_result = self.run_api_test(
                "Get Submissions with Batch Filter",
                "GET",
                f"submissions?batch_id={self.test_batch_id}",
                200
            )
        
        return teacher_result

    def test_re_evaluations_api(self):
        """Test re-evaluation requests API"""
        # Test get re-evaluations
        return self.run_api_test(
            "Get Re-evaluation Requests",
            "GET",
            "re-evaluations",
            200
        )

    def test_insights(self):
        """Test AI insights"""
        return self.run_api_test(
            "AI Insights",
            "GET",
            "analytics/insights",
            200
        )

    def test_duplicate_exam_prevention(self):
        """Test duplicate exam name prevention"""
        if not hasattr(self, 'test_batch_id') or not hasattr(self, 'test_subject_id'):
            print("⚠️  Skipping duplicate exam test - missing batch or subject")
            return None
            
        # Create first exam with specific name
        exam_name = "Test Exam 1"
        exam_data = {
            "batch_id": self.test_batch_id,
            "subject_id": self.test_subject_id,
            "exam_type": "Unit Test",
            "exam_name": exam_name,
            "total_marks": 100.0,
            "exam_date": "2024-01-15",
            "grading_mode": "balanced",
            "questions": [
                {
                    "question_number": 1,
                    "max_marks": 100.0,
                    "rubric": "Test question"
                }
            ]
        }
        
        # Create first exam (should succeed)
        first_result = self.run_api_test(
            "Create First Exam (Test Exam 1)",
            "POST",
            "exams",
            200,
            data=exam_data
        )
        
        if first_result:
            self.test_duplicate_exam_id = first_result.get('exam_id')
            
            # Try to create second exam with same name (should fail)
            duplicate_result = self.run_api_test(
                "Create Duplicate Exam (should fail)",
                "POST", 
                "exams",
                400,  # Should fail with 400
                data=exam_data
            )
            
            # Verify error message contains "already exists"
            if duplicate_result is None:
                # Test the error message by making the request manually
                url = f"{self.base_url}/exams"
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': f'Bearer {self.session_token}'
                }
                
                try:
                    response = requests.post(url, json=exam_data, headers=headers, timeout=10)
                    if response.status_code == 400:
                        error_data = response.json()
                        error_message = error_data.get('detail', '')
                        if "already exists" in error_message.lower():
                            self.log_test("Duplicate Exam Error Message Check", True, f"Correct error message: {error_message}")
                        else:
                            self.log_test("Duplicate Exam Error Message Check", False, f"Unexpected error message: {error_message}")
                    else:
                        self.log_test("Duplicate Exam Error Message Check", False, f"Expected 400, got {response.status_code}")
                except Exception as e:
                    self.log_test("Duplicate Exam Error Message Check", False, f"Request failed: {str(e)}")
            
            return first_result
        
        return None

    def test_exam_deletion(self):
        """Test exam deletion functionality"""
        if not hasattr(self, 'test_duplicate_exam_id'):
            print("⚠️  Skipping exam deletion test - no exam to delete")
            return None
            
        exam_id = self.test_duplicate_exam_id
        
        # First verify exam exists
        verify_result = self.run_api_test(
            "Verify Exam Exists Before Deletion",
            "GET",
            "exams",
            200
        )
        
        if verify_result:
            # Check if our exam is in the list
            exam_found = any(exam.get('exam_id') == exam_id for exam in verify_result)
            if exam_found:
                print(f"✅ Exam {exam_id} found in exam list")
            else:
                print(f"⚠️  Exam {exam_id} not found in exam list")
        
        # Delete the exam
        delete_result = self.run_api_test(
            "Delete Exam",
            "DELETE",
            f"exams/{exam_id}",
            200
        )
        
        if delete_result:
            # Verify exam is deleted by checking exam list
            verify_deleted = self.run_api_test(
                "Verify Exam Deleted",
                "GET", 
                "exams",
                200
            )
            
            if verify_deleted:
                exam_still_exists = any(exam.get('exam_id') == exam_id for exam in verify_deleted)
                if not exam_still_exists:
                    self.log_test("Exam Deletion Verification", True, "Exam successfully removed from list")
                else:
                    self.log_test("Exam Deletion Verification", False, "Exam still exists in list after deletion")
            
            # Try to delete the same exam again (should return 404)
            second_delete = self.run_api_test(
                "Delete Non-existent Exam (should fail)",
                "DELETE",
                f"exams/{exam_id}",
                404  # Should fail with 404
            )
            
            return delete_result
        
        return None

    def test_student_id_validation(self):
        """Test student ID validation rules"""
        print("\n🔍 Testing Student ID Validation...")
        
        # Test valid student ID
        valid_student_data = {
            "email": f"valid.student.{datetime.now().strftime('%H%M%S')}@school.edu",
            "name": "Valid Student",
            "role": "student",
            "student_id": "STU001",
            "batches": [self.test_batch_id] if hasattr(self, 'test_batch_id') else []
        }
        
        valid_result = self.run_api_test(
            "Create Student with Valid ID (STU001)",
            "POST",
            "students",
            200,
            data=valid_student_data
        )
        
        if valid_result:
            self.valid_student_id = valid_result.get('user_id')
            self.valid_student_student_id = "STU001"
        
        # Test short ID (should fail)
        short_id_data = {
            "email": f"short.student.{datetime.now().strftime('%H%M%S')}@school.edu",
            "name": "Short ID Student",
            "role": "student", 
            "student_id": "AB",
            "batches": []
        }
        
        self.run_api_test(
            "Create Student with Short ID (AB) - should fail",
            "POST",
            "students",
            400,  # Should fail
            data=short_id_data
        )
        
        # Test long ID (should fail)
        long_id_data = {
            "email": f"long.student.{datetime.now().strftime('%H%M%S')}@school.edu",
            "name": "Long ID Student",
            "role": "student",
            "student_id": "VERYLONGSTUDENTID123456789",
            "batches": []
        }
        
        self.run_api_test(
            "Create Student with Long ID - should fail",
            "POST",
            "students",
            400,  # Should fail
            data=long_id_data
        )
        
        # Test invalid characters (should fail)
        invalid_char_data = {
            "email": f"invalid.student.{datetime.now().strftime('%H%M%S')}@school.edu",
            "name": "Invalid Char Student",
            "role": "student",
            "student_id": "STU@001",
            "batches": []
        }
        
        self.run_api_test(
            "Create Student with Invalid Characters (STU@001) - should fail",
            "POST",
            "students",
            400,  # Should fail
            data=invalid_char_data
        )
        
        return valid_result

    def test_duplicate_student_id_detection(self):
        """Test duplicate student ID detection with different names"""
        if not hasattr(self, 'valid_student_student_id'):
            print("⚠️  Skipping duplicate student ID test - no valid student created")
            return None
            
        print("\n🔍 Testing Duplicate Student ID Detection...")
        
        # Try to create another student with same ID but different name (should fail)
        duplicate_data = {
            "email": f"duplicate.student.{datetime.now().strftime('%H%M%S')}@school.edu",
            "name": "Jane Smith",  # Different name
            "role": "student",
            "student_id": self.valid_student_student_id,  # Same ID as existing student
            "batches": []
        }
        
        return self.run_api_test(
            "Create Student with Duplicate ID Different Name - should fail",
            "POST",
            "students",
            400,  # Should fail
            data=duplicate_data
        )

    def test_filename_parsing_functionality(self):
        """Test filename parsing for auto-student creation"""
        print("\n🔍 Testing Filename Parsing Logic...")
        
        # This tests the backend logic by examining the parse_student_from_filename function
        # We'll test this indirectly through the upload papers endpoint
        
        # First, we need to create an exam with model answer for testing
        if not hasattr(self, 'test_batch_id') or not hasattr(self, 'test_subject_id'):
            print("⚠️  Skipping filename parsing test - missing batch or subject")
            return None
            
        # Create a test exam for filename parsing
        exam_data = {
            "batch_id": self.test_batch_id,
            "subject_id": self.test_subject_id,
            "exam_type": "Unit Test",
            "exam_name": f"Filename Parse Test {datetime.now().strftime('%H%M%S')}",
            "total_marks": 100.0,
            "exam_date": "2024-01-15",
            "grading_mode": "balanced",
            "questions": [
                {
                    "question_number": 1,
                    "max_marks": 100.0,
                    "rubric": "Test question for filename parsing"
                }
            ]
        }
        
        exam_result = self.run_api_test(
            "Create Exam for Filename Parsing Test",
            "POST",
            "exams",
            200,
            data=exam_data
        )
        
        if exam_result:
            self.filename_test_exam_id = exam_result.get('exam_id')
            print(f"✅ Created test exam for filename parsing: {self.filename_test_exam_id}")
            
            # Note: We can't actually test file upload without real PDF files
            # But we can verify the exam was created successfully
            return exam_result
        
        return None

    def test_auto_add_to_batch_functionality(self):
        """Test auto-add student to batch functionality"""
        print("\n🔍 Testing Auto-Add to Batch Functionality...")
        
        if not hasattr(self, 'test_batch_id'):
            print("⚠️  Skipping auto-add to batch test - no batch created")
            return None
            
        # Create a student and verify they get added to the batch
        timestamp = datetime.now().strftime('%H%M%S')
        auto_student_data = {
            "email": f"auto.batch.student.{timestamp}@school.edu",
            "name": "Auto Batch Student",
            "role": "student",
            "student_id": f"AUTO{timestamp}",
            "batches": [self.test_batch_id]  # Should be added to this batch
        }
        
        student_result = self.run_api_test(
            "Create Student for Auto-Add to Batch Test",
            "POST",
            "students",
            200,
            data=auto_student_data
        )
        
        if student_result:
            student_user_id = student_result.get('user_id')
            
            # Verify student was added to batch by checking batch details
            batch_details = self.run_api_test(
                "Get Batch Details to Verify Student Added",
                "GET",
                f"batches/{self.test_batch_id}",
                200
            )
            
            if batch_details:
                students_list = batch_details.get('students_list', [])
                student_found = any(s.get('user_id') == student_user_id for s in students_list)
                
                if student_found:
                    self.log_test("Student Auto-Added to Batch Verification", True, f"Student {student_user_id} found in batch {self.test_batch_id}")
                else:
                    self.log_test("Student Auto-Added to Batch Verification", False, f"Student {student_user_id} not found in batch {self.test_batch_id}")
                
                return batch_details
            
        return student_result

    def test_comprehensive_student_workflow(self):
        """Test comprehensive student creation and management workflow"""
        print("\n🔍 Testing Comprehensive Student Workflow...")
        
        if not hasattr(self, 'test_batch_id'):
            print("⚠️  Skipping comprehensive workflow test - no batch created")
            return None
            
        # Test 1: Create student with valid format similar to filename parsing
        timestamp = datetime.now().strftime('%H%M%S')
        
        # Test various valid student ID formats
        test_formats = [
            {"id": f"STU{timestamp}1", "name": "John Doe", "expected": True},
            {"id": f"ROLL{timestamp}", "name": "Alice Smith", "expected": True},
            {"id": f"A{timestamp}", "name": "Bob Jones", "expected": True},
        ]
        
        created_students = []
        
        for i, test_case in enumerate(test_formats):
            student_data = {
                "email": f"format.test.{i}.{timestamp}@school.edu",
                "name": test_case["name"],
                "role": "student",
                "student_id": test_case["id"],
                "batches": [self.test_batch_id]
            }
            
            result = self.run_api_test(
                f"Create Student with Format {test_case['id']} ({test_case['name']})",
                "POST",
                "students",
                200 if test_case["expected"] else 400,
                data=student_data
            )
            
            if result and test_case["expected"]:
                created_students.append({
                    "user_id": result.get('user_id'),
                    "student_id": test_case["id"],
                    "name": test_case["name"]
                })
        
        # Test 2: Verify all students are in the batch
        if created_students:
            batch_details = self.run_api_test(
                "Verify All Students Added to Batch",
                "GET",
                f"batches/{self.test_batch_id}",
                200
            )
            
            if batch_details:
                students_in_batch = batch_details.get('students_list', [])
                batch_user_ids = [s.get('user_id') for s in students_in_batch]
                
                all_found = True
                for student in created_students:
                    if student['user_id'] not in batch_user_ids:
                        all_found = False
                        break
                
                if all_found:
                    self.log_test("All Created Students Found in Batch", True, f"All {len(created_students)} students found in batch")
                else:
                    self.log_test("All Created Students Found in Batch", False, "Some students missing from batch")
        
        return created_students

    def cleanup_test_data(self):
        """Clean up test data from MongoDB"""
        print("\n🧹 Cleaning up test data...")
        
        cleanup_commands = f"""
use('test_database');
// Clean up test data
db.users.deleteMany({{email: /test\\.(user|student)\\./}});
db.user_sessions.deleteMany({{session_token: /(test_session|student_session)/}});
db.batches.deleteMany({{name: /(Test Batch|Mathematics Grade|Updated Mathematics|Temp Delete)/}});
db.subjects.deleteMany({{name: /Test Subject/}});
db.exams.deleteMany({{exam_name: /(Test Exam|Algebra Fundamentals|Grading Test)/}});
print('Test data cleaned up');
"""
        
        try:
            with open('/tmp/mongo_cleanup.js', 'w') as f:
                f.write(cleanup_commands)
            
            result = subprocess.run([
                'mongosh', '--quiet', '--file', '/tmp/mongo_cleanup.js'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print("✅ Test data cleaned up")
            else:
                print(f"⚠️  Cleanup warning: {result.stderr}")
                
        except Exception as e:
            print(f"⚠️  Cleanup error: {str(e)}")

    def run_all_tests(self):
        """Run all API tests"""
        print("🚀 Starting GradeSense API Testing")
        print("=" * 50)
        
        # Test health check first (no auth required)
        self.test_health_check()
        
        # Create test user and session
        if not self.create_test_user_and_session():
            print("❌ Failed to create test user - stopping tests")
            return False
        
        # Test authenticated endpoints
        print("\n📋 Testing Authenticated Endpoints")
        print("-" * 30)
        
        # Auth test
        self.test_auth_me()
        
        # CRUD operations
        print("\n📋 Testing Batch Management")
        print("-" * 30)
        self.test_create_batch()
        self.test_duplicate_batch_prevention()
        self.test_get_batches()
        self.test_get_batch_details()
        self.test_update_batch()
        self.test_delete_empty_batch()
        
        print("\n📚 Testing Subject & Student Management")
        print("-" * 30)
        self.test_create_subject()
        self.test_get_subjects()
        
        self.test_create_student()
        self.test_get_students()
        
        print("\n📝 Testing Exam Management with Sub-questions")
        print("-" * 30)
        self.test_create_exam_with_subquestions()
        self.test_grading_modes()
        self.test_get_exams()
        
        print("\n📊 Testing Student Analytics")
        print("-" * 30)
        self.test_student_analytics_api()
        self.test_detailed_student_analytics()
        
        print("\n📋 Testing Submissions & Re-evaluations")
        print("-" * 30)
        self.test_submissions_api()
        self.test_re_evaluations_api()
        
        # Analytics
        print("\n📊 Testing Teacher Analytics Endpoints")
        print("-" * 30)
        self.test_dashboard_analytics()
        self.test_class_report()
        self.test_insights()
        
        # Test new features: Duplicate Prevention & Deletion
        print("\n🔒 Testing Duplicate Prevention & Deletion Features")
        print("-" * 30)
        self.test_duplicate_exam_prevention()
        self.test_exam_deletion()
        
        # Cleanup
        self.cleanup_test_data()
        
        # Print summary
        print("\n" + "=" * 50)
        print(f"📊 Test Summary: {self.tests_passed}/{self.tests_run} tests passed")
        
        if self.tests_passed == self.tests_run:
            print("🎉 All tests passed!")
            return True
        else:
            print(f"⚠️  {self.tests_run - self.tests_passed} tests failed")
            return False

def main():
    tester = GradeSenseAPITester()
    success = tester.run_all_tests()
    
    # Save detailed results
    results = {
        "timestamp": datetime.now().isoformat(),
        "total_tests": tester.tests_run,
        "passed_tests": tester.tests_passed,
        "success_rate": (tester.tests_passed / tester.tests_run * 100) if tester.tests_run > 0 else 0,
        "test_details": tester.test_results
    }
    
    with open('/app/backend_test_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())