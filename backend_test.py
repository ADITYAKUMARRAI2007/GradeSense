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
        student_data = {
            "email": f"test.student.{datetime.now().strftime('%H%M%S')}@example.com",
            "name": "Test Student",
            "role": "student",
            "batches": []
        }
        return self.run_api_test(
            "Create Student",
            "POST",
            "students",
            200,
            data=student_data
        )

    def test_get_students(self):
        """Test get students"""
        return self.run_api_test(
            "Get Students",
            "GET",
            "students",
            200
        )

    def test_create_exam(self):
        """Test exam creation"""
        # Need batch and subject first
        if not hasattr(self, 'test_batch_id') or not hasattr(self, 'test_subject_id'):
            print("⚠️  Skipping exam creation - missing batch or subject")
            return None
            
        exam_data = {
            "batch_id": self.test_batch_id,
            "subject_id": self.test_subject_id,
            "exam_type": "Unit Test",
            "exam_name": f"Test Exam {datetime.now().strftime('%H%M%S')}",
            "total_marks": 100.0,
            "exam_date": "2024-01-15",
            "grading_mode": "balanced",
            "questions": [
                {
                    "question_number": 1,
                    "max_marks": 50.0,
                    "rubric": "Test question 1"
                },
                {
                    "question_number": 2,
                    "max_marks": 50.0,
                    "rubric": "Test question 2"
                }
            ]
        }
        return self.run_api_test(
            "Create Exam",
            "POST",
            "exams",
            200,
            data=exam_data
        )

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

    def test_insights(self):
        """Test AI insights"""
        return self.run_api_test(
            "AI Insights",
            "GET",
            "analytics/insights",
            200
        )

    def cleanup_test_data(self):
        """Clean up test data from MongoDB"""
        print("\n🧹 Cleaning up test data...")
        
        cleanup_commands = f"""
use('test_database');
// Clean up test data
db.users.deleteMany({{email: /test\\.user\\./}});
db.user_sessions.deleteMany({{session_token: /test_session/}});
db.batches.deleteMany({{name: /Test Batch/}});
db.subjects.deleteMany({{name: /Test Subject/}});
db.exams.deleteMany({{exam_name: /Test Exam/}});
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
        self.test_create_batch()
        self.test_get_batches()
        
        self.test_create_subject()
        self.test_get_subjects()
        
        self.test_create_student()
        self.test_get_students()
        
        self.test_create_exam()
        self.test_get_exams()
        
        # Analytics
        print("\n📊 Testing Analytics Endpoints")
        print("-" * 30)
        self.test_dashboard_analytics()
        self.test_class_report()
        self.test_insights()
        
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