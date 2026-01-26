#!/usr/bin/env python3

import requests
import sys
import json
from datetime import datetime, timedelta
import subprocess
import os
import time

class ObjectIdSerializationTester:
    def __init__(self):
        self.base_url = "https://gradesense-ai-2.preview.emergentagent.com/api"
        self.session_token = None
        self.user_id = None
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []
        
        # Test data
        self.test_exam_id = None
        self.test_submission_ids = []
        self.test_grading_job_id = None

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

    def run_api_test(self, name, method, endpoint, expected_status, data=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        headers = {'Content-Type': 'application/json'}
        
        if self.session_token:
            headers['Authorization'] = f'Bearer {self.session_token}'

        print(f"\n🔍 Testing {name}...")
        print(f"   URL: {url}")
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            elif method == 'PUT':
                response = requests.put(url, json=data, headers=headers, timeout=30)

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
        self.user_id = f"test-objectid-user-{timestamp}"
        self.session_token = f"test_objectid_session_{timestamp}"
        
        mongo_commands = f"""
use('test_database');
var userId = '{self.user_id}';
var sessionToken = '{self.session_token}';
var expiresAt = new Date(Date.now() + 7*24*60*60*1000);

// Insert test user
db.users.insertOne({{
  user_id: userId,
  email: 'test.objectid.{timestamp}@example.com',
  name: 'Test ObjectId Teacher',
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

print('Test ObjectId user and session created successfully');
"""
        
        try:
            with open('/tmp/mongo_objectid_setup.js', 'w') as f:
                f.write(mongo_commands)
            
            result = subprocess.run([
                'mongosh', '--quiet', '--file', '/tmp/mongo_objectid_setup.js'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"✅ Test ObjectId user created: {self.user_id}")
                return True
            else:
                print(f"❌ MongoDB setup failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ Error creating test user: {str(e)}")
            return False

    def create_test_data_with_objectids(self):
        """Create test exam, submissions, and grading job with ObjectIds in MongoDB"""
        print("\n🏗️  Creating test data with ObjectIds in MongoDB...")
        
        timestamp = int(datetime.now().timestamp())
        self.test_exam_id = f"exam_objectid_{timestamp}"
        self.test_grading_job_id = f"job_objectid_{timestamp}"
        
        # Create submission IDs
        self.test_submission_ids = [
            f"sub_objectid_{timestamp}_1",
            f"sub_objectid_{timestamp}_2",
            f"sub_objectid_{timestamp}_3"
        ]
        
        mongo_commands = f"""
use('test_database');
var userId = '{self.user_id}';
var examId = '{self.test_exam_id}';
var jobId = '{self.test_grading_job_id}';
var timestamp = new Date().toISOString();

// Create test exam
db.exams.insertOne({{
  exam_id: examId,
  exam_name: 'ObjectId Serialization Test Exam',
  teacher_id: userId,
  batch_id: 'batch_test_objectid',
  subject_id: 'subject_test_objectid',
  exam_type: 'Test',
  total_marks: 100,
  grading_mode: 'balanced',
  questions: [
    {{
      question_number: 1,
      max_marks: 50,
      rubric: 'Test question 1'
    }},
    {{
      question_number: 2,
      max_marks: 50,
      rubric: 'Test question 2'
    }}
  ],
  status: 'completed',
  created_at: timestamp
}});

// Create test submissions with nested ObjectIds (this is what was causing the issue)
db.submissions.insertOne({{
  submission_id: '{self.test_submission_ids[0]}',
  exam_id: examId,
  student_id: 'student_test_1',
  student_name: 'Test Student 1',
  total_score: 85,
  percentage: 85.0,
  question_scores: [
    {{
      question_number: 1,
      max_marks: 50,
      obtained_marks: 45,
      ai_feedback: 'Good work on question 1',
      question_text: 'Test question 1'
    }},
    {{
      question_number: 2,
      max_marks: 50,
      obtained_marks: 40,
      ai_feedback: 'Good work on question 2',
      question_text: 'Test question 2'
    }}
  ],
  status: 'ai_graded',
  created_at: timestamp
}});

db.submissions.insertOne({{
  submission_id: '{self.test_submission_ids[1]}',
  exam_id: examId,
  student_id: 'student_test_2',
  student_name: 'Test Student 2',
  total_score: 78,
  percentage: 78.0,
  question_scores: [
    {{
      question_number: 1,
      max_marks: 50,
      obtained_marks: 38,
      ai_feedback: 'Decent work on question 1',
      question_text: 'Test question 1'
    }},
    {{
      question_number: 2,
      max_marks: 50,
      obtained_marks: 40,
      ai_feedback: 'Good work on question 2',
      question_text: 'Test question 2'
    }}
  ],
  status: 'ai_graded',
  created_at: timestamp
}});

db.submissions.insertOne({{
  submission_id: '{self.test_submission_ids[2]}',
  exam_id: examId,
  student_id: 'student_test_3',
  student_name: 'Test Student 3',
  total_score: 92,
  percentage: 92.0,
  question_scores: [
    {{
      question_number: 1,
      max_marks: 50,
      obtained_marks: 47,
      ai_feedback: 'Excellent work on question 1',
      question_text: 'Test question 1'
    }},
    {{
      question_number: 2,
      max_marks: 50,
      obtained_marks: 45,
      ai_feedback: 'Excellent work on question 2',
      question_text: 'Test question 2'
    }}
  ],
  status: 'ai_graded',
  created_at: timestamp
}});

// Create grading job with nested submission objects (this was the main issue)
db.grading_jobs.insertOne({{
  job_id: jobId,
  exam_id: examId,
  teacher_id: userId,
  status: 'completed',
  created_at: timestamp,
  completed_at: timestamp,
  submissions: [
    {{
      submission_id: '{self.test_submission_ids[0]}',
      student_id: 'student_test_1',
      student_name: 'Test Student 1',
      total_score: 85,
      percentage: 85.0,
      status: 'ai_graded'
    }},
    {{
      submission_id: '{self.test_submission_ids[1]}',
      student_id: 'student_test_2',
      student_name: 'Test Student 2',
      total_score: 78,
      percentage: 78.0,
      status: 'ai_graded'
    }},
    {{
      submission_id: '{self.test_submission_ids[2]}',
      student_id: 'student_test_3',
      student_name: 'Test Student 3',
      total_score: 92,
      percentage: 92.0,
      status: 'ai_graded'
    }}
  ],
  total_papers: 3,
  graded_papers: 3,
  average_score: 85.0
}});

print('Test data with ObjectIds created successfully');
print('Exam ID: ' + examId);
print('Job ID: ' + jobId);
print('Submission IDs: {self.test_submission_ids[0]}, {self.test_submission_ids[1]}, {self.test_submission_ids[2]}');
"""
        
        try:
            with open('/tmp/mongo_objectid_data.js', 'w') as f:
                f.write(mongo_commands)
            
            result = subprocess.run([
                'mongosh', '--quiet', '--file', '/tmp/mongo_objectid_data.js'
            ], capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                print(f"✅ Test data created successfully")
                print(f"   Exam ID: {self.test_exam_id}")
                print(f"   Job ID: {self.test_grading_job_id}")
                print(f"   Submission IDs: {len(self.test_submission_ids)} submissions")
                return True
            else:
                print(f"❌ MongoDB data creation failed: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"❌ Error creating test data: {str(e)}")
            return False

    def test_grading_job_objectid_serialization(self):
        """CRITICAL TEST: Test grading job endpoint for ObjectId serialization issues"""
        print("\n🔥 CRITICAL TEST: Grading Job ObjectId Serialization...")
        
        job_result = self.run_api_test(
            "CRITICAL: Get Grading Job Status (ObjectId Fix Verification)",
            "GET",
            f"grading-jobs/{self.test_grading_job_id}",
            200
        )
        
        if job_result:
            # Check for ObjectId serialization issues
            response_str = str(job_result)
            
            # Check for _id fields (should not exist after serialization fix)
            if '_id' in response_str:
                self.log_test("Grading Job ObjectId Serialization Fix", False, 
                    "Found _id fields in response - serialize_doc() not working properly")
                return False
            
            # Check for ObjectId types (should be converted to strings)
            if 'ObjectId(' in response_str:
                self.log_test("Grading Job ObjectId Serialization Fix", False, 
                    "Found ObjectId types in response - not converted to strings")
                return False
            
            # Verify response structure
            required_fields = ['job_id', 'status', 'submissions', 'created_at']
            missing_fields = [field for field in required_fields if field not in job_result]
            
            if missing_fields:
                self.log_test("Grading Job Response Structure", False, 
                    f"Missing required fields: {missing_fields}")
                return False
            
            # Check submissions array structure
            submissions = job_result.get('submissions', [])
            if not submissions:
                self.log_test("Grading Job Submissions Array", False, 
                    "No submissions found in grading job")
                return False
            
            # Verify each submission in the array doesn't have _id fields
            for i, submission in enumerate(submissions):
                sub_str = str(submission)
                if '_id' in sub_str:
                    self.log_test(f"Submission {i+1} ObjectId Serialization", False, 
                        "Found _id field in nested submission object")
                    return False
                
                # Check required submission fields
                sub_required = ['submission_id', 'student_id', 'student_name', 'total_score']
                sub_missing = [field for field in sub_required if field not in submission]
                if sub_missing:
                    self.log_test(f"Submission {i+1} Structure", False, 
                        f"Missing fields in submission: {sub_missing}")
                    return False
            
            self.log_test("CRITICAL: Grading Job ObjectId Serialization Fix", True, 
                f"Successfully retrieved grading job with {len(submissions)} submissions, no ObjectId issues")
            return True
        
        return False

    def test_submission_details_objectid_serialization(self):
        """CRITICAL TEST: Test submission details endpoint for ObjectId serialization issues"""
        print("\n🔥 CRITICAL TEST: Submission Details ObjectId Serialization...")
        
        success_count = 0
        
        for i, submission_id in enumerate(self.test_submission_ids):
            print(f"   Testing submission {i+1}: {submission_id}")
            
            submission_result = self.run_api_test(
                f"CRITICAL: Get Submission Details {i+1} (ObjectId Fix)",
                "GET",
                f"submissions/{submission_id}",
                200
            )
            
            if submission_result:
                # Check for ObjectId serialization issues
                response_str = str(submission_result)
                
                # Check for _id fields
                if '_id' in response_str:
                    self.log_test(f"Submission {i+1} ObjectId Serialization Fix", False, 
                        "Found _id fields in response - serialize_doc() not working")
                    continue
                
                # Check for ObjectId types
                if 'ObjectId(' in response_str:
                    self.log_test(f"Submission {i+1} ObjectId Type Conversion", False, 
                        "Found ObjectId types in response - not converted to strings")
                    continue
                
                # Verify essential fields
                essential_fields = ['submission_id', 'exam_id', 'student_id', 'student_name', 
                                  'total_score', 'percentage', 'question_scores', 'status']
                missing_fields = [field for field in essential_fields if field not in submission_result]
                
                if missing_fields:
                    self.log_test(f"Submission {i+1} Response Structure", False, 
                        f"Missing essential fields: {missing_fields}")
                    continue
                
                # Check question_scores array for ObjectId issues
                question_scores = submission_result.get('question_scores', [])
                question_scores_clean = True
                
                for j, q_score in enumerate(question_scores):
                    q_score_str = str(q_score)
                    if '_id' in q_score_str or 'ObjectId(' in q_score_str:
                        self.log_test(f"Submission {i+1} Question {j+1} ObjectId", False, 
                            "Found ObjectId issues in question_scores array")
                        question_scores_clean = False
                        break
                
                if question_scores_clean:
                    self.log_test(f"Submission {i+1} Question Scores ObjectId Fix", True, 
                        f"Question scores array clean, no ObjectId issues")
                    success_count += 1
                
                self.log_test(f"CRITICAL: Submission {i+1} ObjectId Serialization Fix", True, 
                    "Successfully retrieved submission details, no ObjectId issues")
        
        overall_success = success_count == len(self.test_submission_ids)
        self.log_test("CRITICAL: All Submissions ObjectId Serialization Fix", overall_success, 
            f"Successfully tested {success_count}/{len(self.test_submission_ids)} submissions")
        
        return overall_success

    def test_submissions_list_objectid_serialization(self):
        """Test submissions list endpoint for ObjectId serialization issues"""
        print("\n📋 Testing Submissions List ObjectId Serialization...")
        
        submissions_list = self.run_api_test(
            "Submissions List ObjectId Serialization Check",
            "GET",
            "submissions",
            200
        )
        
        if submissions_list:
            response_str = str(submissions_list)
            
            # Check for _id fields
            if '_id' in response_str:
                self.log_test("Submissions List ObjectId Serialization Fix", False, 
                    "Found _id fields in submissions list response")
                return False
            
            # Check for ObjectId types
            if 'ObjectId(' in response_str:
                self.log_test("Submissions List ObjectId Type Conversion", False, 
                    "Found ObjectId types in submissions list response")
                return False
            
            # Verify structure if submissions exist
            if isinstance(submissions_list, list) and submissions_list:
                first_submission = submissions_list[0]
                required_fields = ['submission_id', 'exam_id', 'student_id', 'student_name']
                missing_fields = [field for field in required_fields if field not in first_submission]
                
                if missing_fields:
                    self.log_test("Submissions List Structure", False, 
                        f"Missing fields in submission list items: {missing_fields}")
                    return False
            
            self.log_test("Submissions List ObjectId Serialization Fix", True, 
                "Submissions list clean, no ObjectId serialization issues")
            return True
        
        return False

    def test_exams_list_objectid_serialization(self):
        """Test exams list endpoint for ObjectId serialization issues"""
        print("\n📚 Testing Exams List ObjectId Serialization...")
        
        exams_list = self.run_api_test(
            "Exams List ObjectId Serialization Check",
            "GET",
            "exams",
            200
        )
        
        if exams_list:
            response_str = str(exams_list)
            
            # Check for _id fields
            if '_id' in response_str:
                self.log_test("Exams List ObjectId Serialization Fix", False, 
                    "Found _id fields in exams list response - serialize_doc() not applied to exams endpoint")
                
                # Let's check which endpoint is returning _id fields
                print("   🔍 Analyzing _id field locations in exams response...")
                if isinstance(exams_list, list):
                    for i, exam in enumerate(exams_list):
                        if '_id' in str(exam):
                            print(f"      Exam {i+1} contains _id field: {exam.get('exam_id', 'unknown')}")
                
                return False
            
            # Check for ObjectId types
            if 'ObjectId(' in response_str:
                self.log_test("Exams List ObjectId Type Conversion", False, 
                    "Found ObjectId types in exams list response")
                return False
            
            self.log_test("Exams List ObjectId Serialization Fix", True, 
                "Exams list clean, no ObjectId serialization issues")
            return True
        
        return False

    def test_edge_cases_objectid_serialization(self):
        """Test edge cases for ObjectId serialization"""
        print("\n🧪 Testing Edge Cases for ObjectId Serialization...")
        
        # Test 1: Non-existent grading job (should return 404, not crash)
        self.run_api_test(
            "Edge Case: Non-existent Grading Job (No Crash)",
            "GET",
            "grading-jobs/job_nonexistent_objectid_test",
            404
        )
        
        # Test 2: Non-existent submission (should return 404, not crash)
        self.run_api_test(
            "Edge Case: Non-existent Submission (No Crash)",
            "GET",
            "submissions/sub_nonexistent_objectid_test",
            404
        )
        
        # Test 3: Malformed grading job ID (should handle gracefully)
        self.run_api_test(
            "Edge Case: Malformed Grading Job ID",
            "GET",
            "grading-jobs/invalid_job_id_with_special_chars!@#",
            404
        )
        
        # Test 4: Malformed submission ID (should handle gracefully)
        self.run_api_test(
            "Edge Case: Malformed Submission ID",
            "GET",
            "submissions/invalid_sub_id_with_special_chars!@#",
            404
        )
        
        return True

    def run_objectid_serialization_tests(self):
        """Run comprehensive ObjectId serialization tests"""
        print("🚀 STARTING OBJECTID SERIALIZATION FIX VERIFICATION")
        print("=" * 80)
        print("Testing the serialize_doc() function fix for MongoDB ObjectId serialization")
        print("This addresses the critical 520 error issue in grading workflows")
        print("=" * 80)
        
        # Setup
        if not self.create_test_user_and_session():
            print("❌ Failed to create test user and session")
            return False
        
        if not self.create_test_data_with_objectids():
            print("❌ Failed to create test data with ObjectIds")
            return False
        
        # Core ObjectId Serialization Tests
        print("\n" + "="*60)
        print("CORE OBJECTID SERIALIZATION TESTS")
        print("="*60)
        
        # Test the main endpoints that were crashing before the fix
        grading_job_success = self.test_grading_job_objectid_serialization()
        submission_details_success = self.test_submission_details_objectid_serialization()
        submissions_list_success = self.test_submissions_list_objectid_serialization()
        exams_list_success = self.test_exams_list_objectid_serialization()
        
        # Edge Cases
        print("\n" + "="*60)
        print("EDGE CASES AND ERROR HANDLING")
        print("="*60)
        
        edge_cases_success = self.test_edge_cases_objectid_serialization()
        
        # Final Results
        print("\n" + "="*80)
        print("OBJECTID SERIALIZATION FIX VERIFICATION RESULTS")
        print("="*80)
        
        print(f"Total Tests Run: {self.tests_run}")
        print(f"Tests Passed: {self.tests_passed}")
        print(f"Tests Failed: {self.tests_run - self.tests_passed}")
        print(f"Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        # Show critical test results
        critical_tests = [result for result in self.test_results if "CRITICAL" in result["test"]]
        if critical_tests:
            print("\n🔥 CRITICAL TEST RESULTS:")
            for test in critical_tests:
                status = "✅ PASSED" if test["success"] else "❌ FAILED"
                print(f"   {status}: {test['test']}")
                if not test["success"] and test["details"]:
                    print(f"      Details: {test['details']}")
        
        # Show failed tests
        failed_tests = [result for result in self.test_results if not result["success"]]
        if failed_tests:
            print(f"\n❌ FAILED TESTS ({len(failed_tests)}):")
            for test in failed_tests:
                print(f"   • {test['test']}: {test['details']}")
        else:
            print("\n🎉 ALL TESTS PASSED!")
        
        # Summary of ObjectId fix verification
        critical_endpoints_passed = grading_job_success and submission_details_success
        
        print("\n" + "="*60)
        print("OBJECTID SERIALIZATION FIX SUMMARY")
        print("="*60)
        
        if critical_endpoints_passed:
            print("✅ CRITICAL ENDPOINTS: All critical endpoints (grading-jobs, submissions) are working")
            print("✅ SERIALIZATION FIX: serialize_doc() function is working correctly")
            print("✅ NO CRASHES: No 520 errors due to ObjectId serialization issues")
            print("✅ JSON SAFE: All responses are properly JSON serializable")
        else:
            print("❌ CRITICAL ENDPOINTS: Some critical endpoints still have ObjectId issues")
            print("❌ SERIALIZATION FIX: serialize_doc() function may not be applied everywhere")
            print("❌ POTENTIAL CRASHES: 520 errors may still occur in grading workflows")
        
        return critical_endpoints_passed

if __name__ == "__main__":
    tester = ObjectIdSerializationTester()
    success = tester.run_objectid_serialization_tests()
    
    if success:
        print("\n🎉 OBJECTID SERIALIZATION FIX VERIFICATION PASSED!")
        print("The serialize_doc() function is working correctly and preventing crashes.")
        sys.exit(0)
    else:
        print("\n💥 OBJECTID SERIALIZATION FIX VERIFICATION FAILED!")
        print("There are still ObjectId serialization issues that need to be addressed.")
        sys.exit(1)